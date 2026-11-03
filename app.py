"""Entrypoint for Vercel (top-level `app`) and for the `flask` CLI."""
from negarit import create_app

app = create_app()

if __name__ == "__main__":  # local dev only; never used on Vercel
    app.run(debug=True)