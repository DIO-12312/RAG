FROM golang:1.26.2-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /product ./cmd/api
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/* && useradd -u 10001 -m product && mkdir /var/lib/product && chown product:product /var/lib/product
COPY --from=build /product /usr/local/bin/product
USER product
ENTRYPOINT ["product"]
