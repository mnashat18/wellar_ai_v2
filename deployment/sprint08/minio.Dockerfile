# Test-only MinIO Community Edition built from its official, pinned source.
# Building this image requires network access to GitHub and Go module sources.
FROM golang:1.24.8-alpine AS build

ARG MINIO_REF=RELEASE.2025-10-15T17-29-55Z
ARG MINIO_COMMIT=9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a

RUN apk add --no-cache ca-certificates git
WORKDIR /src
RUN git init . \
    && git remote add origin https://github.com/minio/minio.git \
    && git fetch --depth 1 origin "${MINIO_REF}" \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${MINIO_COMMIT}"

RUN mkdir -p /out \
    && LDFLAGS="$(go run buildscripts/gen-ldflags.go)" \
    && CGO_ENABLED=0 go build -p 4 -tags kqueue -trimpath -ldflags "${LDFLAGS}" -o /out/minio .

FROM alpine:3.22
RUN apk add --no-cache ca-certificates \
    && addgroup -S -g 10001 minio \
    && adduser -S -D -H -u 10001 -G minio minio \
    && mkdir -p /data /home/minio/.minio /usr/share/licenses/minio \
    && chown -R minio:minio /data /home/minio

COPY --from=build /out/minio /usr/local/bin/minio
COPY --from=build /src/LICENSE /usr/share/licenses/minio/LICENSE
COPY --from=build /src/NOTICE /usr/share/licenses/minio/NOTICE

ENV HOME=/home/minio
USER 10001:10001
VOLUME ["/data"]
EXPOSE 9000 9001
ENTRYPOINT ["/usr/local/bin/minio"]
CMD ["server", "/data", "--console-address", ":9001"]
