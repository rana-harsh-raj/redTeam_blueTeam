#!/usr/bin/env python3
"""Apply an exact, trace-validated critical-journey overlay to generated inventory."""
from __future__ import annotations
import argparse, hashlib, json, re, subprocess
from pathlib import Path

def load(path): return json.loads(path.read_text())
def dump(path,value): path.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
def sha(data:bytes): return hashlib.sha256(data).hexdigest()
def git(repo,*args): return subprocess.check_output(['git','-C',str(repo),*args],text=True).strip()
def require(ok,msg):
    if not ok: raise SystemExit('runtime-fidelity validation failed: '+msg)

def validate_trace(trace, required, source_sha):
    require(trace.get('schema_version')=='s2p-golden-evidence/v1','unsupported trace schema')
    require(trace.get('source_sha')==source_sha,'trace source SHA does not match spec')
    assertions={x.get('id'):x.get('passed') for x in trace.get('assertions',[])}
    require(all(assertions.get(x) is True for x in required),'required named assertions are absent or failed')
    db=trace.get('database',{}); rows=db.get('replica_trace',[]); kinds={x.get('kind') for x in rows}
    require({'kafka_received','source_handler_returned','kafka_committed','pay_requested','callback_received','source_pubsub'}<=kinds,'raw replica trace kinds incomplete')
    payloads=[]
    for row in rows:
        try: payloads.append((row.get('kind'),json.loads(row.get('payload','{}'))))
        except (TypeError,json.JSONDecodeError): raise SystemExit('runtime-fidelity validation failed: invalid replica_trace payload JSON')
    require(any(k=='kafka_received' and p.get('topic')=='add-tds-entry' for k,p in payloads),'raw Kafka receipt missing')
    statuses={p.get('data',{}).get('Status') for k,p in payloads if k=='source_pubsub' and isinstance(p.get('data'),dict)}
    require({'money_loading_initiated','money_loading_success'}<=statuses,'raw source status trace incomplete')
    tax=db.get('tax_payments',[]); require(any(x.get('internal_status')=='money_loading_success' and x.get('status')=='processing' for x in tax),'terminal source tax-payment state missing')
    require(bool(db.get('records')),'source record persistence missing')
    require(bool(db.get('vendorpayment_payout')),'source payout association missing')
    audit=trace.get('boundary',{}).get('audit',[]); paths=[x.get('path','') for x in audit]
    require(any(x.startswith('/v1/payouts_internal/') and x.endswith('/tax-payment-id') for x in paths),'raw tag-back boundary call missing')
    for path in ('/v1/contacts_internal/','/v1/vendor-payments/verify-otp','/v1/banking_accounts_internal','/v1/internalContactPayout/'):
        require(path in paths,f'raw boundary call missing: {path}')
    payouts=trace.get('boundary',{}).get('payouts',[])
    require(any(x.get('status')=='created' and x.get('tax_payment_id') is None for x in payouts),'boundary remittance payout missing')
    require(trace.get('callback',{}).get('code')==200 and trace.get('callback',{}).get('body',{}).get('success') is True,'callback outcome missing')
    return {'kafka_ingest','tds_persisted','pay_requested','remittance_boundary','remittance_created','money_loading_initiated','callback_received','money_loading_success'}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--domain-root',type=Path,default=Path(__file__).resolve().parents[1]); ap.add_argument('--source-root',type=Path,required=True); ap.add_argument('--spec',type=Path); ns=ap.parse_args()
    domain=ns.domain_root.resolve(); spec_path=(ns.spec or domain/'spec/runtime-fidelity.json').resolve(); spec=load(spec_path)
    vp=ns.source_root/'vendor-payments'; require(git(vp,'rev-parse','HEAD')==spec['source_sha'],'pinned vendor-payments HEAD differs')
    trace_path=domain/spec['trace_artifact']; trace=load(trace_path); satisfied=validate_trace(trace,spec['required_assertions'],spec['source_sha'])
    compile_evidence=load(domain/spec['compile_verification']); require(compile_evidence.get('source_sha')==spec['source_sha'] and compile_evidence.get('verified'),'source compile verification is absent')
    deviation_ids=set(re.findall(r'^\s*- id:\s*(\S+)',(domain/'spec/declared-deviations.yaml').read_text(),re.M))
    nodes=spec['actual_source_symbols']+spec['replacement_nodes']; source_evidence=[]; trace_digest=sha(trace_path.read_bytes())
    for node in nodes:
        base=vp if node['repository']=='vendor-payments' else domain; path=base/node['path']; require(path.is_file(),f"missing source file {node['path']}")
        lines=path.read_text().splitlines(); a=node['line_start']; b=node['line_end']; require(1<=a<=b<=len(lines),f"bad range for {node['id']}")
        excerpt=('\n'.join(lines[a-1:b])+'\n').encode(); require(sha(excerpt)==node['range_sha256'],f"source range changed for {node['id']}")
        require(re.search(r'\b'+re.escape(node['symbol'])+r'\b',lines[a-1]),f"symbol signature mismatch for {node['id']}")
        require(set(node['trace_requirements'])<=satisfied,f"unsatisfied raw evidence for {node['id']}")
        require(node['status'] in {'ACTUAL_SOURCE_RUNNING','CONTRACT_FAITHFUL_REPLACEMENT'},f"invalid status for {node['id']}")
        if node['status']=='CONTRACT_FAITHFUL_REPLACEMENT': require(set(node['deviation_ids'])<=deviation_ids,f"undeclared adapter deviation for {node['id']}")
        source_evidence.append({'evidence_id':'ev:runtime-source:'+node['id'],'origin':'exact_source_range','repository':node['repository'],'sha':spec['source_sha'] if node['repository']=='vendor-payments' else sha(path.read_bytes()),'path':node['path'],'line_start':a,'line_end':b,'excerpt_sha256':node['range_sha256']})
    trace_evidence=[{'evidence_id':'ev:runtime-trace:'+key,'origin':'raw_runtime_trace','path':spec['trace_artifact'],'artifact_sha256':trace_digest,'selector':key} for key in sorted(satisfied)]
    node_ids={x['id'] for x in nodes}
    for edge in spec['semantic_edges']:
        require(edge['source'] in node_ids and edge['target'] in node_ids,f"edge endpoint absent: {edge['id']}"); require(set(edge['trace_requirements'])<=satisfied,f"edge trace evidence absent: {edge['id']}")
    overlay_nodes=[]
    for node in nodes:
        n=dict(node); n.update({'category':'runtime_fidelity_symbol' if node['repository']=='vendor-payments' else 'runtime_fidelity_adapter','representation_status':node['status'],'mandatory_journey':True,'potential_journey_relevance':True,'evidence_ids':['ev:runtime-source:'+node['id']]+['ev:runtime-trace:'+x for x in node['trace_requirements']], 'execution_scope':'function-level path participation; no line-coverage claim'}); overlay_nodes.append(n)
    overlay_edges=[]
    for edge in spec['semantic_edges']:
        e=dict(edge); e['evidence_ids']=['ev:runtime-source:'+x for x in edge['source_evidence']]+['ev:runtime-trace:'+x for x in edge['trace_requirements']]; overlay_edges.append(e)
    overlay={'schema_version':1,'spec':str(spec_path.relative_to(domain)),'trace_artifact':spec['trace_artifact'],'trace_sha256':trace_digest,'source_sha':spec['source_sha'],'raw_accepted_flag_observed':trace.get('accepted'),'raw_evidence_validated_independently':True,'raw_denominator_unchanged':True,'mandatory_closure_count':len(overlay_nodes),'actual_source_running_count':sum(x['status']=='ACTUAL_SOURCE_RUNNING' for x in nodes),'contract_faithful_replacement_count':sum(x['status']=='CONTRACT_FAITHFUL_REPLACEMENT' for x in nodes),'execution_claim':spec['policy']['execution_claim'],'nodes':overlay_nodes,'edges':overlay_edges}
    dump(domain/'inventory/runtime-fidelity-overlay.json',overlay)
    inv_path=domain/spec['generated_inventory']; inv=load(inv_path); prior={x['id']:x for x in inv['items'] if not x.get('runtime_fidelity_overlay')}
    for n in overlay_nodes:
        item=dict(n); item['runtime_fidelity_overlay']=True; prior[item['id']]=item
    inv['items']=sorted(prior.values(),key=lambda x:x['id']); inv['runtime_fidelity_overlay']={'artifact':'runtime-fidelity-overlay.json','excluded_from_detector_denominator':True,'mandatory_closure_count':len(overlay_nodes),'raw_detector_denominator':inv['coverage']['denominator']}; dump(inv_path,inv)
    evidence_path=domain/'inventory/evidence-index.json'; evidence=load(evidence_path); retained={x['evidence_id']:x for x in evidence['evidence'] if not x['evidence_id'].startswith('ev:runtime-')}
    for x in source_evidence+trace_evidence: retained[x['evidence_id']]=x
    evidence['evidence']=sorted(retained.values(),key=lambda x:x['evidence_id']); dump(evidence_path,evidence)
    graph_path=domain/spec['generated_graph']; graph=load(graph_path); graph_nodes={x['id']:x for x in graph['nodes'] if not x.get('runtime_fidelity_overlay')}; graph_edges={x['id']:x for x in graph['edges'] if not x.get('runtime_fidelity_overlay')}
    for n in overlay_nodes: graph_nodes[n['id']]={'id':n['id'],'type':n['category'],'label':n['symbol'],'status':n['status'],'repository':n['repository'],'evidence_ids':n['evidence_ids'],'mandatory_journey':True,'runtime_fidelity_overlay':True}
    for e in overlay_edges: graph_edges[e['id']]={**e,'runtime_fidelity_overlay':True}
    graph['nodes']=sorted(graph_nodes.values(),key=lambda x:x['id']); graph['edges']=sorted(graph_edges.values(),key=lambda x:x['id']); graph['runtime_fidelity_overlay']={'artifact':'../inventory/runtime-fidelity-overlay.json','mandatory_closure_count':len(overlay_nodes),'raw_trace_validated':True}; dump(graph_path,graph)
    print(json.dumps({'overlay':str(domain/'inventory/runtime-fidelity-overlay.json'),'mandatory_closure_count':len(overlay_nodes),'actual_source_running_count':overlay['actual_source_running_count'],'contract_faithful_replacement_count':overlay['contract_faithful_replacement_count']},sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
