"""Dedicated BFF process. Infrastructure supplies secrets; raw URL access logging is disabled."""

import uvicorn


def main() -> None:
    uvicorn.run(
        "maezo.portal.api.production:create_production_app",
        factory=True,
        host="0.0.0.0",
        port=8080,
        access_log=False,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
