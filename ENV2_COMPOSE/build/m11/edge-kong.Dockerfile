# M11: the REAL Razorpay edge gateway image, built from the pinned razorpay/edge clone (Dockerfile at its root,
# reproduced here step by step). The ONLY change is the base-image registry: the source pins
# ${DOCKER_REPO}/razorpay-external/kong:3.4.2-ubuntu (c.rzp.io / harbor mirror, not reachable from this host);
# the public docker.io kong:3.4.2-ubuntu is the same upstream Kong release. MAXMIND fetch is off (the committed
# GeoLite2-Country.mmdb is used, exactly the source's FETCH_MAXMIND_KEY=false branch). Build context = the edge clone.
FROM kong:3.4.2-ubuntu
ARG GIT_COMMIT
USER root
RUN apt-get update && apt-get upgrade -y
ENV TZ=Asia/Kolkata DEBIAN_FRONTEND=noninteractive
RUN apt-get install -y curl tzdata git-all libmaxminddb0 libmaxminddb-dev gcc libc-dev openssl libssl-dev zlib1g-dev
RUN apt-get install -y nettle-dev
RUN cp /usr/share/zoneinfo/Asia/Kolkata /etc/localtime && echo "Asia/Kolkata" > /etc/timezone
RUN mkdir -p /usr/local/kong/kong-plugins
ADD kong-plugins /usr/local/kong/kong-plugins
ADD kong-utils /usr/local/kong/kong-utils
ADD kong.conf /etc/kong/kong.conf
ADD luarocks_install.sh /
RUN git config --global url.https://github.com/.insteadOf git://github.com/
WORKDIR /usr/local/kong/
RUN mkdir geo-ip
ADD GeoLite2-Country.mmdb /usr/local/kong/geo-ip
# retry wrapper only: raw.githubusercontent.com (the luarocks mirror the source pins) intermittently resets TLS reads from this host
RUN for i in 1 2 3 4 5 6; do /luarocks_install.sh && break; echo "luarocks_install.sh attempt $i failed; retrying"; sleep 8; done && luarocks list | grep -q lua-resty-http
RUN for i in 1 2 3 4 5 6; do luarocks install --only-server https://raw.githubusercontent.com/rocks-moonscript-org/moonrocks-mirror/daab2726276e3282dc347b89a42a5107c3500567 luaossl OPENSSL_DIR=/usr/local/kong CRYPTO_DIR=/usr/local/kong && break; echo "luaossl attempt $i failed; retrying"; sleep 8; done && luarocks list | grep -q luaossl
RUN echo "${GIT_COMMIT}" > /usr/local/kong/commit.txt
RUN echo 'location = /commit.txt { alias /usr/local/kong/commit.txt; default_type text/plain; }' > /usr/local/kong/commit-location.conf
USER kong
