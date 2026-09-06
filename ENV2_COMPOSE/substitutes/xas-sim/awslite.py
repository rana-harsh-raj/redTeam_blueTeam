"""Minimal stdlib LocalStack SQS/SNS client for arena substitutes.

LocalStack does not verify SigV4 signatures; it only parses the Credential
scope to pick region/account (verified live: a request signed with
`Signature=deadbeef` and `Credential=test/20260907/ap-south-1/sqs/aws4_request`
returned HTTP 200 for GetQueueUrl). This module therefore emits a SigV4-SHAPED
Authorization header with a constant signature. It is arena-only: the endpoint
is the internal `localstack:4566` service name and nothing here can reach a real
AWS endpoint (rzp-arena is internal:true).

SQS uses the JSON protocol (X-Amz-Target: AmazonSQS.<Action>); SNS uses the
query protocol (form-encoded, XML response) because LocalStack SNS has no JSON
protocol. Queue URLs are always built as <endpoint>/<account>/<name> so they
resolve inside the arena network (LocalStack's own GetQueueUrl answer uses the
host-only name sqs.ap-south-1.localhost.localstack.cloud).
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

ENDPOINT = "http://localstack:4566"
REGION = "ap-south-1"
ACCOUNT = "000000000000"


class AwsError(Exception):
    def __init__(self, status, body):
        super().__init__("aws %s: %s" % (status, body[:300]))
        self.status = status
        self.body = body


def _auth_headers(service):
    now = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return {
        "Authorization": "AWS4-HMAC-SHA256 Credential=arena/%s/%s/%s/aws4_request, SignedHeaders=host;x-amz-date, Signature=0"
                         % (now[:8], REGION, service),
        "X-Amz-Date": now,
    }


def _post(url, data, headers, timeout):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, (exc.read() or b"").decode("utf-8", "replace")


def queue_url(name, endpoint=ENDPOINT, account=ACCOUNT):
    return "%s/%s/%s" % (endpoint.rstrip("/"), account, name)


def queue_arn(name, region=REGION, account=ACCOUNT):
    return "arn:aws:sqs:%s:%s:%s" % (region, account, name)


def sqs(action, body, endpoint=ENDPOINT, timeout=25):
    headers = {"Content-Type": "application/x-amz-json-1.0", "X-Amz-Target": "AmazonSQS.%s" % action}
    headers.update(_auth_headers("sqs"))
    status, text = _post(endpoint.rstrip("/") + "/", json.dumps(body).encode(), headers, timeout)
    if status != 200:
        raise AwsError(status, text)
    return json.loads(text or "{}")


def sqs_ensure_queue(name, endpoint=ENDPOINT):
    """Idempotent: CreateQueue with the same name/attributes returns the existing queue."""
    try:
        sqs("GetQueueUrl", {"QueueName": name}, endpoint)
        return queue_url(name, endpoint), False
    except AwsError as exc:
        if "QueueDoesNotExist" not in exc.body and "NonExistentQueue" not in exc.body:
            raise
    sqs("CreateQueue", {"QueueName": name}, endpoint)
    return queue_url(name, endpoint), True


def sqs_send(name, message_body, endpoint=ENDPOINT):
    return sqs("SendMessage", {"QueueUrl": queue_url(name, endpoint), "MessageBody": message_body}, endpoint)


def sqs_receive(name, max_messages=10, wait_seconds=5, visibility=60, endpoint=ENDPOINT):
    out = sqs("ReceiveMessage", {"QueueUrl": queue_url(name, endpoint), "MaxNumberOfMessages": max_messages,
                                 "WaitTimeSeconds": wait_seconds, "VisibilityTimeout": visibility},
              endpoint, timeout=wait_seconds + 20)
    return out.get("Messages") or []


def sqs_delete(name, receipt_handle, endpoint=ENDPOINT):
    return sqs("DeleteMessage", {"QueueUrl": queue_url(name, endpoint), "ReceiptHandle": receipt_handle}, endpoint)


def sqs_attributes(name, endpoint=ENDPOINT):
    out = sqs("GetQueueAttributes", {"QueueUrl": queue_url(name, endpoint), "AttributeNames": ["All"]}, endpoint)
    return out.get("Attributes") or {}


def sns(action, params, endpoint=ENDPOINT, timeout=25):
    form = dict(params)
    form["Action"] = action
    form["Version"] = "2010-03-31"
    headers = {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"}
    headers.update(_auth_headers("sns"))
    status, text = _post(endpoint.rstrip("/") + "/", urllib.parse.urlencode(form).encode(), headers, timeout)
    if status != 200:
        raise AwsError(status, text)
    return text


def _xml_tag(text, tag):
    m = re.search(r"<%s>(.*?)</%s>" % (tag, tag), text, re.S)
    return m.group(1).strip() if m else None


def sns_ensure_topic(name, endpoint=ENDPOINT):
    """CreateTopic is idempotent for an existing name (returns the same ARN)."""
    return _xml_tag(sns("CreateTopic", {"Name": name}, endpoint), "TopicArn")


def sns_subscribe_sqs(topic_arn, queue_name, raw=False, endpoint=ENDPOINT):
    """Subscribe an SQS queue to a topic. RawMessageDelivery=false reproduces the
    production shape (SNS envelope with a `Message` string), which the ledger
    worker unwraps in job_sqs/base.go extractRawContent."""
    params = {"TopicArn": topic_arn, "Protocol": "sqs", "Endpoint": queue_arn(queue_name),
              "Attributes.entry.1.key": "RawMessageDelivery", "Attributes.entry.1.value": "true" if raw else "false"}
    return _xml_tag(sns("Subscribe", params, endpoint), "SubscriptionArn")


def sns_list_subscriptions(topic_arn, endpoint=ENDPOINT):
    text = sns("ListSubscriptionsByTopic", {"TopicArn": topic_arn}, endpoint)
    return re.findall(r"<Endpoint>(.*?)</Endpoint>", text)


def sns_publish(topic_arn, message, endpoint=ENDPOINT):
    return _xml_tag(sns("Publish", {"TopicArn": topic_arn, "Message": message}, endpoint), "MessageId")
