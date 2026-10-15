# Employee Attendance & Analytics API

A REST API built with **FastAPI** and **MongoDB** for employee attendance management, attendance analytics, and query execution-plan inspection.

## Features

- Employee and attendance data management.
- Attendance listing and filtering.
- Employee monthly attendance analytics.
- Department-level attendance summaries and trends.
- Late-arrival leaderboard.
- MongoDB query execution plans and execution statistics.
- MongoDB indexes to support efficient queries and enforce data integrity.

## Tech Stack

- Python
- FastAPI
- PyMongo
- MongoDB 6.0 or newer
- Uvicorn

## Prerequisites

- Python installed on your system.
- MongoDB 6.0 or newer, either locally or through MongoDB Atlas.

## Setup

### 1. Clone the repository

```bash
git clone <YOUR_PUBLIC_REPOSITORY_URL>
cd <YOUR_REPOSITORY_FOLDER>
```

### 2. Create and activate a virtual environment

**Windows PowerShell:**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**Linux/macOS:**

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file in the project root:

```dotenv
MONGO_URI=your_mongodb_connection_string
MONGO_DB=attendance_db
```

Replace the placeholder with your MongoDB connection string. Never commit `.env` or publish database credentials.

### 5. Load sample data (if needed)

```bash
python sample_seed.py
```

Run the seed script only when appropriate for your database, since it may insert sample records.

### 6. Start the API

```bash
python -m uvicorn app.main:app --port 8000 --reload
```

The API will be available at:

- **Swagger UI:** http://127.0.0.1:8000/docs
- **OpenAPI schema:** http://127.0.0.1:8000/openapi.json

## API Endpoints

The API includes employee, attendance, analytics, and administrative operations.

The query-plan endpoint supports these options:

- `GET /admin/explain/attendance_list`
- `GET /admin/explain/employee_monthly`
- `GET /admin/explain/department_summary`
- `GET /admin/explain/late_leaderboard`
- `GET /admin/explain/department_trend`

Use Swagger UI to view the complete API contract, parameters, and response schemas.

## Database Indexes

The application creates indexes at startup for employee lookups, department queries, attendance listing, date-based analytics, and late-arrival queries. Unique indexes enforce employee-code and employee/date uniqueness.

## Project Structure

```text
candidate_kit/
├── app/
│   └── main.py
├── sample_data/
├── .gitignore
├── DATA_MODEL.md
├── DECISIONS.md
├── REVIEW.md
├── requirements.txt
├── sample_seed.py
└── README.md
```

## Submission

Submit the public Git repository containing the completed `app/main.py`, `requirements.txt`, `README.md`, `REVIEW.md`, and `DECISIONS.md`, along with any required supporting files.

Do not commit `.env`, database credentials, or other secrets. Follow the assignment instructions regarding prohibited files and repository contents.
