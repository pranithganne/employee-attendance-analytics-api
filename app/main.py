"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
import json
from bson import json_util
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from pymongo.errors import DuplicateKeyError
from typing import Optional, Literal
from dotenv import load_dotenv
from pymongo import MongoClient
from decimal import Decimal, ROUND_HALF_UP
from datetime import date

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]


def create_indexes():
    # Employees
    db.employees.create_index(
        [("emp_code", 1)],
        unique=True,
        name="uq_employee_code",
    )
    db.employees.create_index(
        [("department", 1), ("joined_on", 1)],
        name="ix_employee_department_joined",
    )

    # Attendance: enforce one record per employee per attendance day.
    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
        name="uq_attendance_employee_date",
    )

    # Attendance listing and date-based analytics.
    db.attendance_logs.create_index(
        [("date", -1), ("emp_code", 1)],
        name="ix_attendance_date_employee",
    )
    db.attendance_logs.create_index(
        [("date", 1), ("status", 1)],
        name="ix_attendance_date_status",
    )
    db.attendance_logs.create_index(
        [("date", 1), ("late_minutes", -1), ("emp_code", 1)],
        name="ix_attendance_date_late",
    )




IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

@app.on_event("startup")
def startup_indexes():
    create_indexes()
# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def to_ist(value: datetime) -> datetime:
    """Convert a MongoDB datetime or aware datetime to IST."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(IST)


def truncate_to_seconds(value: datetime) -> datetime:
    """Normalize an instant to IST and discard fractional seconds."""
    return to_ist(value).replace(microsecond=0)


def shift_start_datetime(
    punch_in: datetime,
    shift_start: str,
    shift_end: str,
) -> datetime:
    """Return the correct shift start, including overnight shifts."""
    local_in = to_ist(punch_in)
    start_hour, start_minute = map(int, shift_start.split(":"))
    end_hour, end_minute = map(int, shift_end.split(":"))

    overnight = (end_hour, end_minute) <= (
        start_hour, start_minute
    )

    shift_date = local_in.date()
    if overnight and (local_in.hour, local_in.minute) < (
        end_hour, end_minute
    ):
        shift_date -= timedelta(days=1)

    return datetime(
        shift_date.year,
        shift_date.month,
        shift_date.day,
        start_hour,
        start_minute,
        tzinfo=IST,
    )


def compute_late_minutes(
    punch_in: datetime,
    shift_start: str,
    shift_end: str = "18:30",
) -> int:
    """Apply the 10-minute grace period and floor elapsed minutes."""
    local_in = to_ist(punch_in)
    shift_start_at = shift_start_datetime(
        local_in, shift_start, shift_end
    )

    elapsed_seconds = (
        local_in - shift_start_at
    ).total_seconds()

    if elapsed_seconds <= 10 * 60:
        return 0

    return int(elapsed_seconds // 60)


def compute_work_hours(
    punch_in: datetime,
    punch_out: datetime,
) -> float:
    """Calculate hours using decimal half-up rounding."""
    seconds = (
        to_ist(punch_out) - to_ist(punch_in)
    ).total_seconds()

    hours = Decimal(str(seconds)) / Decimal("3600")
    return float(
        hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )


def compute_overtime(
    punch_out: datetime,
    shift_end: str,
    date_str: str,
    shift_start: str = "09:30",
) -> int:
    """Count whole overtime minutes only when they reach 30."""
    local_out = to_ist(punch_out)
    end_hour, end_minute = map(int, shift_end.split(":"))
    start_hour, start_minute = map(int, shift_start.split(":"))

    overnight = (end_hour, end_minute) <= (
        start_hour, start_minute
    )

    shift_date = date.fromisoformat(date_str)
    if overnight:
        shift_date += timedelta(days=1)
    shift_end_at = datetime(
        shift_date.year,
        shift_date.month,
        shift_date.day,
        end_hour,
        end_minute,
        tzinfo=IST,
    )

    elapsed_seconds = (
        local_out - shift_end_at
    ).total_seconds()
    whole_minutes = int(elapsed_seconds // 60)

    return whole_minutes if whole_minutes >= 30 else 0



# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(
        max_length=120,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(
        default="09:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
    )
    shift_end: str = Field(
        default="18:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
    )
    joined_on: str = Field(
        pattern=r"^\d{4}-\d{2}-\d{2}$"
    )

    @model_validator(mode="after")
    def validate_employee(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")

        try:
            datetime.strptime(self.joined_on, "%Y-%m-%d")
        except ValueError:
            raise ValueError("joined_on must be a valid YYYY-MM-DD date")

        return self




class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(
        default=None,
        strict=True,
        ge=100_000_000_000,
        le=4_102_444_800_000,
    )
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(
        default=None,
        strict=True,
        ge=100_000_000_000,
        le=4_102_444_800_000,
    )


class RegularizeIn(BaseModel):
    status: Optional[Literal[
        "PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"
    ]] = None

    punch_in: Optional[int] = Field(
        default=None,
        strict=True,
        ge=100_000_000_000,
        le=4_102_444_800_000,
    )

    punch_out: Optional[int] = Field(
        default=None,
        strict=True,
        ge=100_000_000_000,
        le=4_102_444_800_000,
    )

    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_supplied_timestamps(self):
        for field in ("punch_in", "punch_out"):
            if (
                field in self.model_fields_set
                and getattr(self, field) is None
            ):
                raise ValueError(
                    f"{field} must be an epoch-millisecond integer"
                )
        return self


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #

@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        )





@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)

    try:
        result = db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists",
        )

    doc["created_at"] = int(
        doc["created_at"].timestamp() * 1000
    )
    doc["_id"] = str(result.inserted_id)

    doc.pop("_id", None)
    return doc


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = {}

    if department:
        query["department"] = department

    total = db.employees.count_documents(query)

    skip = (page - 1) * page_size

    items = list(
        db.employees.find(query, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }




@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="unknown employee")

    # Convert epoch milliseconds to UTC, then truncate to whole seconds.
    if body.punched_at is None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
    else:
        now = datetime.fromtimestamp(
            body.punched_at / 1000, tz=timezone.utc
        ).replace(microsecond=0)

    local_in = now.astimezone(IST)
    start_hour, start_minute = map(
        int, emp["shift_start"].split(":")
    )
    end_hour, end_minute = map(
        int, emp["shift_end"].split(":")
    )

    # An early-morning punch for an overnight shift belongs to
    # the shift that started on the previous calendar day.
    overnight = (end_hour, end_minute) <= (
        start_hour, start_minute
    )
    attendance_day = local_in.date()

    if overnight and (
        local_in.hour, local_in.minute
    ) < (end_hour, end_minute):
        attendance_day -= timedelta(days=1)

    attendance_date = attendance_day.isoformat()

    # MongoDB stores BSON datetimes in UTC.
    stored_punch_in = now.replace(tzinfo=None)

    doc = {
        "emp_code": body.emp_code,
        "date": attendance_date,
        "status": body.status,
        "punch_in": stored_punch_in,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(
            now, emp["shift_start"], emp["shift_end"]
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    # The unique (emp_code, date) index must be created at startup.
    # It prevents concurrent requests from creating duplicate records.
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="already punched in for this date",
        )

    # The API contract returns epoch milliseconds, not ISO timestamps.
    response = dict(doc)
    response["punch_in"] = int(now.timestamp() * 1000)
    response["punch_out"] = None
    return response


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    # Find the employee
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="unknown employee")

    # Resolve the requested punch-out time
    if body.punched_at is None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
    else:
        now = datetime.fromtimestamp(
            body.punched_at / 1000, tz=timezone.utc
        ).replace(microsecond=0)

    # Find an attendance record that has not been punched out
    local_now = now.astimezone(IST)
    start_hour, start_minute = map(
        int, emp["shift_start"].split(":")
    )
    end_hour, end_minute = map(
        int, emp["shift_end"].split(":")
    )

    overnight = (end_hour, end_minute) <= (
        start_hour, start_minute
    )

    attendance_day = local_now.date()
    if overnight and (
        local_now.hour, local_now.minute
    ) < (end_hour, end_minute):
        attendance_day -= timedelta(days=1)

    attendance_date = attendance_day.isoformat()

    record = db.attendance_logs.find_one({
        "emp_code": body.emp_code,
        "date": attendance_date,
        "punch_out": None,
    })

    if not record:
        raise HTTPException(
            status_code=409,
            detail="no open attendance record to punch out",
        )

    punch_in_at = record["punch_in"].replace(tzinfo=timezone.utc)

    if now <= punch_in_at:
        raise HTTPException(
            status_code=422,
            detail="punch-out must be after punch-in",
        )

    work_hours = compute_work_hours(punch_in_at, now)

    shift_start_at = shift_start_datetime(
        punch_in_at, emp["shift_start"], emp["shift_end"]
    )

    shift_end_at = shift_start_at + timedelta(
        hours=(
            (end_hour * 60 + end_minute)
            - (start_hour * 60 + start_minute)
        ) % (24 * 60)
    )

    elapsed_work_seconds = (now - punch_in_at).total_seconds()
    half_day = work_hours < 4.50

    overtime_minutes = compute_overtime(
        now, emp["shift_end"], attendance_date,
        emp["shift_start"]
    )

    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": now.replace(tzinfo=None),
                "work_hours": work_hours,
                "half_day": half_day,
                "overtime_minutes": overtime_minutes,
            }
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="attendance record was already updated",
        )

    return {
        "emp_code": body.emp_code,
        "date": attendance_date,
        "punch_in": int(punch_in_at.timestamp() * 1000),
        "punch_out": int(now.timestamp() * 1000),
        "work_hours": work_hours,
        "late_minutes": record.get("late_minutes", 0),
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
    }



@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    status: Optional[Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must be on or before date_to")

    query = {}

    if emp_code:
        query["emp_code"] = emp_code
    if status:
        query["status"] = status
    if date_from or date_to:
        query["date"] = {}
        if date_from:
            query["date"]["$gte"] = date_from.isoformat()
        if date_to:
            query["date"]["$lte"] = date_to.isoformat()

    total = db.attendance_logs.count_documents(query)

    docs = list(
        db.attendance_logs.find(query, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    def to_epoch_ms(value):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp() * 1000)

    for record in docs:
        for field in ("punch_in", "punch_out"):
            record[field] = to_epoch_ms(record.get(field))

    return {
        "items": docs,
        "total": total,
        "page": page,
        "page_size": page_size,
    }

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizeIn,
):
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="date must use YYYY-MM-DD format",
        )

    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="unknown employee")

    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date,
    })
    if not record:
        raise HTTPException(
            status_code=404,
            detail="attendance record not found",
        )

    original_status = record["status"]
    original_in = record.get("punch_in")
    original_out = record.get("punch_out")

    new_status = (
        body.status
        if "status" in body.model_fields_set
        else original_status
    )

    if new_status in ("ABSENT", "LEAVE"):
        if "punch_in" in body.model_fields_set or (
            "punch_out" in body.model_fields_set
        ):
            raise HTTPException(
                status_code=422,
                detail="ABSENT and LEAVE cannot include punch times",
            )
        new_in = None
        new_out = None
    else:
        if "punch_in" in body.model_fields_set:
            new_in = datetime.fromtimestamp(
                body.punch_in / 1000, tz=timezone.utc
            ).replace(microsecond=0).replace(tzinfo=None)
        else:
            new_in = original_in

        if "punch_out" in body.model_fields_set:
            new_out = datetime.fromtimestamp(
                body.punch_out / 1000, tz=timezone.utc
            ).replace(microsecond=0).replace(tzinfo=None)
        else:
            new_out = original_out

        if new_in is None:
            raise HTTPException(
                status_code=422,
                detail="presence status requires punch_in",
            )

        # Stored MongoDB datetimes are naive UTC.
        new_in_utc = new_in.replace(tzinfo=timezone.utc)
        if new_in_utc.astimezone(IST).date().isoformat() != date:
            raise HTTPException(
                status_code=422,
                detail="punch_in must fall on the attendance date",
            )

        if new_out is not None:
            new_out_utc = new_out.replace(tzinfo=timezone.utc)
            elapsed = (new_out_utc - new_in_utc).total_seconds()

            if elapsed <= 0 or elapsed > 24 * 3600:
                raise HTTPException(
                    status_code=422,
                    detail="punch_out must be after punch_in and within 24 hours",
                )

    if new_status in ("ABSENT", "LEAVE"):
        late_minutes = 0
        work_hours = None
        overtime_minutes = 0
        half_day = False
    else:
        new_in_utc = new_in.replace(tzinfo=timezone.utc)
        late_minutes = compute_late_minutes(
            new_in_utc, emp["shift_start"], emp["shift_end"]
        )

        if new_out is None:
            work_hours = None
            overtime_minutes = 0
            half_day = False
        else:
            new_out_utc = new_out.replace(tzinfo=timezone.utc)
            work_hours = compute_work_hours(new_in_utc, new_out_utc)
            overtime_minutes = compute_overtime(
                new_out_utc,
                emp["shift_end"],
                date,
                emp["shift_start"],
            )
            half_day = work_hours < 4.50

    def epoch_ms(value):
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return int(value.timestamp() * 1000)
        return value

    before = {
        "status": original_status,
        "punch_in": original_in,
        "punch_out": original_out,
        "work_hours": record.get("work_hours"),
        "late_minutes": record.get("late_minutes", 0),
        "overtime_minutes": record.get("overtime_minutes", 0),
        "half_day": record.get("half_day", False),
    }

    after = {
        "status": new_status,
        "punch_in": new_in,
        "punch_out": new_out,
        "work_hours": work_hours,
        "late_minutes": late_minutes,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
    }

    changes = {}
    for key in before:
        old_value = before[key]
        new_value = after[key]

        if key in ("punch_in", "punch_out"):
            old_value = epoch_ms(old_value)
            new_value = epoch_ms(new_value)

        if old_value != new_value:
            changes[key] = {
                "from": old_value,
                "to": new_value,
            }

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="request does not change the record",
        )

    now = datetime.now(timezone.utc).replace(microsecond=0)
    history_entry = {
        "at": now.replace(tzinfo=None),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    # Optimistic concurrency: a simultaneous correction must not
    # overwrite another correction or lose its audit history.
    query = {
        "_id": record["_id"],
        "status": original_status,
        "punch_in": original_in,
        "punch_out": original_out,
    }

    if "history" in record:
        query["history"] = record["history"]
    else:
        query["history"] = {"$exists": False}

    result = db.attendance_logs.update_one(
        query,
        {
            "$set": {
                "status": new_status,
                "punch_in": new_in,
                "punch_out": new_out,
                "work_hours": work_hours,
                "late_minutes": late_minutes,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            },
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="record changed concurrently; retry the correction",
        )

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    updated.pop("_id", None)

    for field in ("punch_in", "punch_out"):
        updated[field] = epoch_ms(updated.get(field))

    for entry in updated.get("history", []):
        entry["at"] = epoch_ms(entry["at"])
        for field, change in entry["changes"].items():
            if field in ("punch_in", "punch_out"):
                change["from"] = epoch_ms(change["from"])
                change["to"] = epoch_ms(change["to"])

    return updated



@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    # Validate that the employee exists.
    emp = db.employees.find_one(
        {"emp_code": emp_code},
        {"_id": 0, "emp_code": 1, "joined_on": 1},
    )
    if not emp:
        raise HTTPException(status_code=404, detail="unknown employee")

    month_start = f"{month}-01"
    year, month_number = map(int, month.split("-"))
    if month_number == 12:
        next_month = f"{year + 1}-01-01"
    else:
        next_month = f"{year}-{month_number + 1:02d}-01"

    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {"$gte": month_start, "$lt": next_month},
            }
        },
        {
            "$set": {
                "weekday": {
                    "$dayOfWeek": {
                        "$dateFromString": {
                            "dateString": "$date",
                            "format": "%Y-%m-%d",
                        }
                    }
                }
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$weekday", [2, 3, 4, 5, 6]]},
                                    {
                                        "$in": [
                                            "$status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "LEAVE"]},
                            1,
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$late_minutes", 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                },
            }
        },
    ]

    result = list(db.attendance_logs.aggregate(pipeline))
    summary = result[0] if result else {}

    # Count weekdays from the employee's joining date using MongoDB.
    joined_on = emp["joined_on"]
    working_pipeline = [
        {"$match": {"emp_code": emp_code}},
        {
            "$project": {
                "_id": 0,
                "working_days": {
                    "$let": {
                        "vars": {
                            "start": {
                                "$max": [
                                    {"$dateFromString": {"dateString": "$joined_on"}},
                                    {"$dateFromString": {"dateString": month_start}},
                                ]
                            },
                            "end": {
                                "$dateFromString": {"dateString": next_month}
                            },
                        },
                        "in": {
                            "$size": {
                                "$filter": {
                                    "input": {
                                        "$map": {
                                            "input": {
                                                "$range": [
                                                    0,
                                                    {
                                                        "$max": [
                                                            0,
                                                            {
                                                                "$dateDiff": {
                                                                    "startDate": "$$start",
                                                                    "endDate": "$$end",
                                                                    "unit": "day",
                                                                }
                                                            },
                                                        ]
                                                    },
                                                ]
                                            },
                                            "as": "offset",
                                            "in": {
                                                "$dateAdd": {
                                                    "startDate": "$$start",
                                                    "unit": "day",
                                                    "amount": "$$offset",
                                                }
                                            },
                                        }
                                    },
                                    "as": "day",
                                    "cond": {
                                        "$in": [
                                            {"$dayOfWeek": "$$day"},
                                            [2, 3, 4, 5, 6],
                                        ]
                                    },
                                }
                            }
                        },
                    }
                },
            }
        },
    ]

    working_result = list(db.employees.aggregate(working_pipeline))
    working_days = working_result[0]["working_days"] if working_result else 0

    present_days = round(float(summary.get("present_days", 0)), 2)
    attendance_pct = (
        round(present_days / working_days * 100, 4)
        if working_days
        else None
    )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": summary.get("leave_days", 0),
        "late_count": summary.get("late_count", 0),
        "total_late_minutes": summary.get("total_late_minutes", 0),
        "total_overtime_minutes": summary.get("total_overtime_minutes", 0),
        "attendance_pct": attendance_pct,
    }



@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
):
    year, month_number = map(int, month.split("-"))

    if month_number == 12:
        next_month = f"{year + 1}-01-01"
    else:
        next_month = f"{year}-{month_number + 1:02d}-01"

    month_end = (
        datetime.strptime(next_month, "%Y-%m-%d").date()
        - timedelta(days=1)
    ).isoformat()

    employee_match = {"joined_on": {"$lte": month_end}}
    if department is not None:
        employee_match["department"] = department

    pipeline = [
        {"$match": employee_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"employee_code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$employee_code"]},
                                    {"$gte": ["$date", f"{month}-01"]},
                                    {"$lt": ["$date", next_month]},
                                ]
                            }
                        }
                    },
                    {
                        "$set": {
                            "_day_of_week": {
                                "$dayOfWeek": {
                                    "$dateFromString": {
                                        "dateString": "$date",
                                        "format": "%Y-%m-%d",
                                    }
                                }
                            }
                        }
                    },
                ],
                "as": "logs",
            }
        },
        {
            "$unwind": {
                "path": "$logs",
                "preserveNullAndEmptyArrays": True,
            }
        },
        {
            "$group": {
                "_id": "$department",
                "employee_codes": {"$addToSet": "$emp_code"},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$logs._day_of_week", [2, 3, 4, 5, 6]]},
                                    {
                                        "$in": [
                                            "$logs.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$logs.half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "work_hours_values": {
                    "$push": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$logs.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {"$ne": ["$logs.work_hours", None]},
                                ]
                            },
                            "$logs.work_hours",
                            None,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$logs.late_minutes", 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$logs.late_minutes", 0]}
                },
                "leave_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "LEAVE"]},
                            1,
                            0,
                        ]
                    }
                },
                "on_duty_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "ON_DUTY"]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": {"$size": "$employee_codes"},
                "present_days": 1,
                "work_hours_values": 1,
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1,
            }
        },
        {"$sort": {"department": 1}},
    ]

    rows = list(db.employees.aggregate(pipeline))
    items = []

    for row in rows:
        hours = [
            value
            for value in row.pop("work_hours_values", [])
            if value is not None
        ]

        row["present_days"] = round(
            float(row["present_days"]), 2
        )
        row["avg_work_hours"] = (
            round(sum(hours) / len(hours), 2)
            if hours
            else None
        )
        items.append(row)

    return {"month": month, "items": items}



@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None,
):
    year, month_number = map(int, month.split("-"))

    if month_number == 12:
        next_month = f"{year + 1}-01-01"
    else:
        next_month = f"{year}-{month_number + 1:02d}-01"

    pipeline = [
        {
            "$match": {
                "date": {"$gte": f"{month}-01", "$lt": next_month},
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]

    if department is not None:
        pipeline.append({
            "$match": {"employee.department": department}
        })

    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}}
                },
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "late_count": 1,
            }
        },
        {
            "$sort": {
                "total_late_minutes": -1,
                "emp_code": 1,
            }
        },
    ])

    items = list(db.attendance_logs.aggregate(pipeline))

    return {"month": month, "items": items}




@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to: str = Query(...),
):
    try:
        start_day = date.fromisoformat(from_date)
        end_day = date.fromisoformat(to)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="from and to must be valid YYYY-MM-DD dates",
        )

    if start_day.isoformat() != from_date or end_day.isoformat() != to:
        raise HTTPException(
            status_code=422,
            detail="from and to must use YYYY-MM-DD format",
        )

    if end_day < start_day:
        raise HTTPException(
            status_code=422,
            detail="to must be on or after from",
        )

    days = (end_day - start_day).days + 1
    if days > 92:
        raise HTTPException(
            status_code=422,
            detail="date range cannot exceed 92 days",
        )

    if db.employees.count_documents({"department": department}) == 0:
        raise HTTPException(
            status_code=404,
            detail="unknown department",
        )

    start_dt = datetime.combine(start_day, datetime.min.time())
    end_dt = datetime.combine(end_day, datetime.min.time())

    pipeline = [
        {"$match": {"department": department}},
        {"$group": {"_id": None}},
        {
            "$project": {
                "_id": 0,
                "day": {
                    "$map": {
                        "input": {"$range": [0, days]},
                        "as": "offset",
                        "in": {
                            "$dateAdd": {
                                "startDate": {
                                    "$dateFromString": {
                                        "dateString": from_date
                                    }
                                },
                                "unit": "day",
                                "amount": "$$offset",
                            }
                        },
                    }
                },
            }
        },
        {"$unwind": "$day"},
        {
            "$set": {
                "date": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$day",
                    }
                },
                "is_working_day": {
                    "$in": [{"$dayOfWeek": "$day"}, [2, 3, 4, 5, 6]]
                },
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {"current_day": "$date"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$department", department]},
                                    {"$lte": ["$joined_on", "$$current_day"]},
                                ]
                            }
                        }
                    },
                    {"$count": "count"},
                ],
                "as": "headcount_result",
            }
        },
        {
            "$set": {
                "headcount": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$headcount_result.count", 0]},
                        0,
                    ]
                }
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"current_day": "$date"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {"$eq": ["$date", "$$current_day"]}
                        }
                    },
                    {
                        "$lookup": {
                            "from": "employees",
                            "localField": "emp_code",
                            "foreignField": "emp_code",
                            "as": "employee",
                        }
                    },
                    {"$unwind": "$employee"},
                    {"$match": {"employee.department": department}},
                    {
                        "$group": {
                            "_id": None,
                            "present_count": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$in": [
                                                "$status",
                                                ["PRESENT", "WFH", "ON_DUTY"],
                                            ]
                                        },
                                        {
                                            "$cond": [
                                                {"$eq": ["$half_day", True]},
                                                0.5,
                                                1,
                                            ]
                                        },
                                        0,
                                    ]
                                }
                            },
                            "late_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$gt": ["$late_minutes", 0]},
                                        1,
                                        0,
                                    ]
                                }
                            },
                        }
                    },
                ],
                "as": "daily",
            }
        },
        {
            "$set": {
                "present_count": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$daily.present_count", 0]},
                        0,
                    ]
                },
                "late_count": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$daily.late_count", 0]},
                        0,
                    ]
                },
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {
                            "$divide": [
                                {
                                    "$floor": {
                                        "$add": [
                                            {
                                                "$multiply": [
                                                    {
                                                        "$divide": [
                                                            "$present_count",
                                                            "$headcount",
                                                        ]
                                                    },
                                                    10000,
                                                ]
                                            },
                                            0.5,
                                        ]
                                    }
                                },
                                10000,
                            ]
                        },
                        None,
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": {
                    "$cond": [
                        {"$eq": ["$moving_avg_7d", None]},
                        None,
                        {
                            "$divide": [
                                {
                                    "$floor": {
                                        "$add": [
                                            {
                                                "$multiply": [
                                                    "$moving_avg_7d",
                                                    10000,
                                                ]
                                            },
                                            0.5,
                                        ]
                                    }
                                },
                                10000,
                            ]
                        },
                    ]
                },
            }
        },
        {"$sort": {"date": 1}},
    ]

    items = list(db.employees.aggregate(pipeline))
    return {"department": department, "items": items}



@app.get("/admin/explain/{endpoint}")
def explain_endpoint(endpoint: str):
    allowed = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }

    if endpoint not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"endpoint must be one of: {', '.join(sorted(allowed))}",
        )

    if endpoint == "attendance_list":
        query = db.attendance_logs.find(
            {"date": {"$gte": "2026-01-01", "$lte": "2026-01-31"}}
        ).sort([("date", -1), ("emp_code", 1)]).limit(20)

        plan = query.explain()

    elif endpoint == "employee_monthly":
        pipeline = [
            {
                "$match": {
                    "emp_code": "EMP0001",
                    "date": {
                        "$gte": "2026-01-01",
                        "$lt": "2026-02-01",
                    },
                }
            }
        ]
        plan = db.attendance_logs.database.command(
            "explain",
            {
                "aggregate": db.attendance_logs.name,
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

    elif endpoint == "department_summary":
        plan = db.employees.database.command(
            "explain",
            {
                "find": db.employees.name,
                "filter": {"department": "Engineering"},
            },
        verbosity="executionStats",
        )

    elif endpoint == "late_leaderboard":
        pipeline = [
            {
                "$match": {
                    "date": {
                        "$gte": "2026-01-01",
                        "$lt": "2026-02-01",
                    },
                    "late_minutes": {"$gt": 0},
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {"$sum": "$late_minutes"},
                }
            },
        ]
        plan = db.attendance_logs.database.command(
            "explain",
            {
                "aggregate": db.attendance_logs.name,
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

    else:
        pipeline = [
            {
                "$match": {
                    "date": {
                        "$gte": "2026-01-01",
                        "$lte": "2026-01-31",
                    }
                }
            }
        ]
        plan = db.attendance_logs.database.command(
            "explain",
            {
                "aggregate": db.attendance_logs.name,
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

    return json.loads(json_util.dumps(plan))


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
