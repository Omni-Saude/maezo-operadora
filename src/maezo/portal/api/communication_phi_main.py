"""Dedicated PHI GET process; raw URL access logging is disabled."""

import uvicorn


def main() -> None:
    uvicorn.run(
        "maezo.gateway.communications.production:create_phi_production_app",
        factory=True,
        host="0.0.0.0",
        port=8081,
        access_log=False,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
