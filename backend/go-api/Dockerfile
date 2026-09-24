FROM golang:1.26.2-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /product ./cmd/api
RUN CGO_ENABLED=0 go build -o /observability-retention ./cmd/observability-retention
RUN CGO_ENABLED=0 go build -o /product-admin-role ./cmd/admin-role
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates wget && rm -rf /var/lib/apt/lists/* && useradd -u 10001 -m product && mkdir -p /var/lib/product /var/tempo-data /var/tempo-overrides && chown -R product:product /var/lib/product /var/tempo-data /var/tempo-overrides
COPY --from=build /product /usr/local/bin/product
COPY --from=build /observability-retention /usr/local/bin/observability-retention
COPY --from=build /product-admin-role /usr/local/bin/product-admin-role
USER product
ENTRYPOINT ["product"]
