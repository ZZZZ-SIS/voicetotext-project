# Security Notes

- Do not commit API keys, OAuth credentials, tokens, cookies, or local configuration files.
- `credentials.json`, `token.json`, `facebook_cookies.txt`, and `config.json` are intentionally excluded by `.gitignore`.
- Releases should be produced through GitHub Actions so that build provenance attestation can be generated.
