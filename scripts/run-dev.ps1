$ErrorActionPreference = "Stop"

# Start the RAG Earthly target in the background, then bring up the Go product
# containers and keep the Vue dev server attached to this terminal.
$volume = "rag-product_product-keys"
docker volume inspect $volume *> $null
if ($LASTEXITCODE -ne 0) {
    docker volume create $volume | Out-Null
}
$rag = Start-Process -FilePath "make" -ArgumentList "docker-up" -PassThru
try {
    docker compose -f compose.product.yml up -d --build
    npm --prefix apps/web run dev -- --host 127.0.0.1
}
finally {
    if ($rag -and -not $rag.HasExited) {
        Stop-Process -Id $rag.Id -Force -ErrorAction SilentlyContinue
    }
}
