# DataGenie

**Describe your business. Let AI build your database.**

A shop owner, farmer or school admin describes their business in plain words. Gemma 4 designs the database, the owner approves it, and DataGenie builds a working dashboard with forms, tables and charts. No SQL, no code.

## What it does

- Gemma 4 picks and adapts a database blueprint (school, supermarket, farm, pharmacy, restaurant) from a plain-language description
- Every AI design is validated before anything is created, and the user approves it first
- Dashboard with add, edit and delete, charts, Excel and CSV import and export, public share link with QR, installable as a PWA
- GenieAI: ask questions ("how many students do I have?") or change the structure ("link classes to teachers"). Common requests are instant and need no AI; Gemma handles the rest
- Organizations with roles and member logins. In a school, a teacher sees only the students in their own classes and can enter grades, and a student sees only their own grades
- Gemma never writes SQL. It returns small plans that the backend validates and runs with parameterized queries

## Stack

Python, Flask, Supabase PostgreSQL, Gemma 4 through the Google Gemini API, plain HTML/CSS/JS.

## Run locally

    git clone https://github.com/onlineconnection/datagenie.git
    cd datagenie
    python -m venv venv && source venv/bin/activate
    pip install -r requirements.txt
    python app.py

Create a .env file first with these variables:

    GEMMA_API_KEY=your-gemini-api-key
    GEMMA_MODEL=gemma-4-26b-a4b-it
    GEMMA_MODEL_BACKUP=gemma-4-31b-it
    DATABASE_URL=your-supabase-session-pooler-url
    SECRET_KEY=a-long-random-string

## License

MIT. See the LICENSE file.
