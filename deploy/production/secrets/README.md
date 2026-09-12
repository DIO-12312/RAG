# Production secret layout

This directory documents files that must be created by the server operator or a
secret manager. It intentionally contains no secret value and must not be used
as the real secret directory. Set `PRODUCTION_SECRETS_DIR` to an absolute,
host-only directory such as `/etc/rag-mvp/secrets`.

```text
search-guard/node/ca.pem
search-guard/node/node.pem
search-guard/node/node-key.pem
search-guard/node/admin.pem
search-guard/node/admin-key.pem
search-guard/node/rag_mvp_password
search-guard/client/ca.pem
search-guard/client/rag_mvp_password
mysql/rag_mysql_password
mysql/rag_mysql_root_password
mysql/product_mysql_password
mysql/product_mysql_root_password
runtime/rag_mysql_dsn
runtime/product_mysql_dsn
product/encryption.key
product/jwt.key
```

`rag_mysql_dsn` and `product_mysql_dsn` are full DSNs read only inside their
respective application containers; they must use the passwords supplied to the
matching MySQL initialization files. `product/encryption.key` is the single
shared, base64-encoded 32-byte AES key used by Go to encrypt Dataset model
profiles and by Python to decrypt them; never create a second RAG copy that can
drift. `product/jwt.key` is at least 32 bytes. They are also the required
`PRODUCT_ENCRYPTION_KEY` and `PRODUCT_JWT_SECRET` values in file form. Do not
place model-provider API keys in this layout: they are stored encrypted through
the authenticated product settings API.

Compose `secrets:` is a **file-backed, read-only bind mount** here, not encrypted
Docker Swarm Secret storage. Docker Compose ignores the service-level `uid`,
`gid`, and `mode` fields for file sources, so host ownership is part of the
runtime contract. Keep the tree outside Git, root-owned with directories `0700`,
and provision files with these numeric owners and modes:

| Files | Host owner | Mode |
| --- | --- | --- |
| `search-guard/node/node-key.pem`, `search-guard/node/rag_mvp_password` | `1000:1000` (Elasticsearch) | `0600` |
| `search-guard/node/admin-key.pem` | `root:root` | `0600` |
| Search Guard CA and certificate files | `root:root` | `0644` |
| `search-guard/client/rag_mvp_password` | `10001:10001` (RAG) | `0600` |
| MySQL password files | `root:root` | `0600` |
| Runtime DSNs, `product/encryption.key`, `product/jwt.key` | `10001:10001` (RAG/Go) | `0600` |

The Python and Go images intentionally use UID/GID `10001`; the Elasticsearch
image uses UID/GID `1000`. The production validator requires both runtime
password copies and all private keys to have no group/other permission bits and
checks certificate identity. The Elasticsearch service receives only its node
key and runtime password; only the root-running bootstrap receives the admin
certificate and key. Docker daemon/root and members of the host `docker` group
can still access every source file. Grant that group sparingly and rotate keys
only with a tested database/key backup and restore procedure.
