# Root-level delegation for local development
# This ensures that running `python app.py` still starts the server on port 5000.

from api.index import app

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
