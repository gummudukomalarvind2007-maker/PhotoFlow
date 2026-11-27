"""Development entrypoint so Python can import the PhotoFlow backend package."""

from photoflow_backend.app import main


if __name__ == "__main__":
    raise SystemExit(main())
