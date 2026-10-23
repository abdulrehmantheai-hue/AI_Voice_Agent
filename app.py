"""CareCloud challenge demo: authenticated patient API and Vapi integration.
Use synthetic patient information only; not a HIPAA-ready healthcare product.
"""
import json
import logging
import os
import re
import secrets
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError, field_validator, model_validator
from sqlalchemy import Date, DateTime, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./patients.db')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = 'postgresql+psycopg://' + DATABASE_URL[len('postgres://'):]
elif DATABASE_URL.startswith('postgresql://'):
    DATABASE_URL = 'postgresql+psycopg://' + DATABASE_URL[len('postgresql://'):]
IS_SQLITE = DATABASE_URL.startswith('sqlite')
engine = create_engine(DATABASE_URL, connect_args={'check_same_thread': False} if IS_SQLITE else {}, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
API_KEY = os.getenv('API_KEY', '')
DASHBOARD_PASSWORD = os.getenv('DASHBOARD_PASSWORD', '')
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET', '')
COOKIE_SECURE = os.getenv('COOKIE_SECURE', 'false').lower() == 'true'
logger = logging.getLogger(__name__)

STATES = set('AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC'.split())
NAME_PATTERN = re.compile(r"^[A-Za-z]+(?:[\-'][A-Za-z]+)*$")
PHONE_PATTERN = re.compile(r'^[2-9]\d{2}[2-9]\d{6}$')
ZIP_PATTERN = re.compile(r'^\d{5}(?:-\d{4})?$')

class Base(DeclarativeBase):
    pass

class Patient(Base):
    __tablename__ = 'patients'
    patient_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    first_name: Mapped[str] = mapped_column(String(50), nullable=False)
    last_name: Mapped[str] = mapped_column(String(50), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    sex: Mapped[str] = mapped_column(String(20), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(255))
    address_line_1: Mapped[str] = mapped_column(String(255), nullable=False)
    address_line_2: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    zip_code: Mapped[str] = mapped_column(String(10), nullable=False)
    insurance_provider: Mapped[str | None] = mapped_column(String(120))
    insurance_member_id: Mapped[str | None] = mapped_column(String(80))
    preferred_language: Mapped[str] = mapped_column(String(60), default='English', nullable=False)
    emergency_contact_name: Mapped[str | None] = mapped_column(String(120))
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class PatientFields(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    first_name: str = Field(min_length=1, max_length=50)
    last_name: str = Field(min_length=1, max_length=50)
    date_of_birth: date
    sex: Literal['Male', 'Female', 'Other', 'Decline to Answer']
    phone_number: str
    email: EmailStr | None = None
    address_line_1: str = Field(min_length=1, max_length=255)
    address_line_2: str | None = Field(default=None, max_length=255)
    city: str = Field(min_length=1, max_length=100)
    state: str
    zip_code: str
    insurance_provider: str | None = Field(default=None, max_length=120)
    insurance_member_id: str | None = Field(default=None, max_length=80)
    preferred_language: str = Field(default='English', min_length=1, max_length=60)
    emergency_contact_name: str | None = Field(default=None, max_length=120)
    emergency_contact_phone: str | None = None

    @field_validator('first_name', 'last_name')
    @classmethod
    def valid_name(cls, v):
        if not NAME_PATTERN.fullmatch(v):
            raise ValueError('Use alphabetic letters, hyphens or apostrophes only')
        return v

    @field_validator('date_of_birth')
    @classmethod
    def valid_dob(cls, v):
        if v > date.today():
            raise ValueError('Date of birth must not be in the future')
        return v

    @field_validator('phone_number', 'emergency_contact_phone')
    @classmethod
    def valid_phone(cls, v):
        if v is None:
            return v
        v = re.sub(r'[^0-9]', '', v)
        if len(v) == 11 and v[0] == '1':
            v = v[1:]
        if not PHONE_PATTERN.fullmatch(v):
            raise ValueError('Provide a valid US ten-digit phone number')
        return v

    @field_validator('state')
    @classmethod
    def valid_state(cls, v):
        v = v.upper()
        if v not in STATES:
            raise ValueError('Use a valid two-letter US state code')
        return v

    @field_validator('zip_code')
    @classmethod
    def valid_zip(cls, v):
        if not ZIP_PATTERN.fullmatch(v):
            raise ValueError('Use US five-digit ZIP or ZIP+4 format')
        return v

    @field_validator('insurance_member_id')
    @classmethod
    def valid_member(cls, v):
        if v is not None and not re.fullmatch(r'[A-Za-z0-9]+', v):
            raise ValueError('Insurance member ID must be alphanumeric')
        return v

    @model_validator(mode='after')
    def contact_pair(self):
        if self.emergency_contact_phone and not self.emergency_contact_name:
            raise ValueError('Emergency contact name is required when their phone is supplied')
        return self

class PatientUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    first_name: str | None = None
    last_name: str | None = None
    date_of_birth: date | None = None
    sex: Literal['Male', 'Female', 'Other', 'Decline to Answer'] | None = None
    phone_number: str | None = None
    email: EmailStr | None = None
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    insurance_provider: str | None = None
    insurance_member_id: str | None = None
    preferred_language: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None


def session():
    with SessionLocal() as db:
        yield db

def authorize(request: Request, x_api_key: Annotated[str | None, Header()] = None):
    if not API_KEY:
        raise HTTPException(503, 'API_KEY is not configured')
    if request.session.get('dashboard') is True:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(401, 'Invalid or missing X-API-Key')

def record(p: Patient):
    return {k.name: (getattr(p, k.name).isoformat() if isinstance(getattr(p, k.name), (date, datetime)) else getattr(p, k.name)) for k in Patient.__table__.columns}

def success(data, code=200):
    return JSONResponse({'data': data, 'error': None}, status_code=code)

def lookup(db: Session, patient_id: str):
    try:
        uuid.UUID(patient_id)
    except (TypeError, ValueError):
        raise HTTPException(400, 'Invalid UUID')
    p = db.get(Patient, patient_id)
    if not p or p.deleted_at:
        raise HTTPException(404, 'Patient not found')
    return p

def create_patient(db, data):
    values = PatientFields.model_validate(data).model_dump()
    p = Patient(**values)
    db.add(p)
    commit_or_rollback(db)
    db.refresh(p)
    return p

def commit_or_rollback(db: Session):
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

def update_patient(db, p, updates):
    data = {k: getattr(p, k) for k in PatientFields.model_fields}
    data.update(updates)
    valid = PatientFields.model_validate(data).model_dump()
    for k, v in valid.items():
        setattr(p, k, v)
    p.updated_at = datetime.now(timezone.utc)
    commit_or_rollback(db)
    db.refresh(p)
    return p

app = FastAPI(title='CareCloud Patient Intake Demo', version='1.0.0', description='Synthetic test patient information only')
app.add_middleware(SessionMiddleware, secret_key=os.getenv('SESSION_SECRET') or (API_KEY + DASHBOARD_PASSWORD + WEBHOOK_SECRET or secrets.token_urlsafe(32)), https_only=COOKIE_SECURE, same_site='lax')

ERROR_CODES = {
    400: 'BAD_REQUEST',
    401: 'UNAUTHORIZED',
    403: 'FORBIDDEN',
    404: 'NOT_FOUND',
    422: 'VALIDATION_ERROR',
    500: 'INTERNAL_SERVER_ERROR',
}

def validation_fields(errors):
    fields = []
    for error in errors:
        location = error.get('loc', ())
        field = '.'.join(str(part) for part in location if part not in ('body', 'query', 'path', 'header'))
        message = error['msg']
        if message.startswith('Value error, '):
            message = message[len('Value error, '):]
        fields.append({'field': field or 'request', 'message': message})
    return fields

@app.exception_handler(HTTPException)
async def http_error(_req, e):
    status_code = e.status_code
    message = e.detail if status_code < 500 and isinstance(e.detail, str) else 'Internal server error'
    code = ERROR_CODES.get(status_code, f'HTTP_{status_code}')
    return JSONResponse(
        {'data': None, 'error': {'code': code, 'message': message}},
        status_code=status_code,
    )

@app.exception_handler(RequestValidationError)
async def input_error(_req, e):
    return JSONResponse(
        {'data': None, 'error': {
            'code': 'VALIDATION_ERROR',
            'message': 'Patient validation failed',
            'fields': validation_fields(e.errors()),
        }},
        status_code=422,
    )

@app.exception_handler(ValidationError)
async def patient_validation_error(_req, e):
    return JSONResponse(
        {'data': None, 'error': {
            'code': 'VALIDATION_ERROR',
            'message': 'Patient validation failed',
            'fields': validation_fields(e.errors()),
        }},
        status_code=422,
    )

@app.exception_handler(Exception)
async def unexpected_error(_req, e):
    logger.error('Unexpected API error: %s', type(e).__name__)
    return JSONResponse(
        {'data': None, 'error': {
            'code': 'INTERNAL_SERVER_ERROR',
            'message': 'Internal server error',
        }},
        status_code=500,
    )

@app.get('/health')
def health():
    return success({'status': 'ok'})

@app.get('/patients', dependencies=[Depends(authorize)])
def list_patients(last_name: str | None = None, date_of_birth: date | None = None, phone_number: str | None = None, db: Session = Depends(session)):
    stmt = select(Patient).where(Patient.deleted_at.is_(None))
    if last_name:
        stmt = stmt.where(Patient.last_name.ilike(last_name.strip()))
    if date_of_birth:
        stmt = stmt.where(Patient.date_of_birth == date_of_birth)
    if phone_number:
        number = re.sub(r'\D', '', phone_number)
        if len(number) == 11 and number.startswith('1'):
            number = number[1:]
        stmt = stmt.where(Patient.phone_number == number)
    return success([record(p) for p in db.scalars(stmt.order_by(Patient.created_at.desc())).all()])

@app.get('/patients/{patient_id}', dependencies=[Depends(authorize)])
def get_patient(patient_id: str, db: Session = Depends(session)):
    return success(record(lookup(db, patient_id)))

@app.post('/patients', dependencies=[Depends(authorize)], status_code=201)
def post_patient(payload: PatientFields, db: Session = Depends(session)):
    return success(record(create_patient(db, payload.model_dump())), 201)

@app.put('/patients/{patient_id}', dependencies=[Depends(authorize)])
def put_patient(patient_id: str, payload: PatientUpdate, db: Session = Depends(session)):
    changes = payload.model_dump(exclude_unset=True)
    return success(record(update_patient(db, lookup(db, patient_id), changes)))

@app.delete('/patients/{patient_id}', dependencies=[Depends(authorize)])
def delete_patient(patient_id: str, db: Session = Depends(session)):
    p = lookup(db, patient_id)
    p.deleted_at = datetime.now(timezone.utc)
    p.updated_at = datetime.now(timezone.utc)
    commit_or_rollback(db)
    return success({'patient_id': patient_id, 'deleted': True})

@app.get('/')
def home():
    return FileResponse(Path(__file__).parent / 'static' / 'index.html')

@app.post('/dashboard/login')
async def dashboard_login(request: Request):
    if not DASHBOARD_PASSWORD:
        raise HTTPException(503, 'DASHBOARD_PASSWORD is not configured')
    body = await request.json()
    if not secrets.compare_digest(str(body.get('password', '')), DASHBOARD_PASSWORD):
        raise HTTPException(401, 'Wrong password')
    request.session['dashboard'] = True
    return success({'authenticated': True})

@app.post('/dashboard/logout')
async def dashboard_logout(request: Request):
    request.session.clear()
    return success({'authenticated': False})

@app.get('/dashboard/me')
async def dashboard_me(request: Request):
    return success({'authenticated': bool(request.session.get('dashboard'))})

@app.post('/vapi/webhook')
async def vapi_webhook(request: Request, db: Session = Depends(session)):
    # Vapi Function tools should use header X-Webhook-Secret, configured in tool secret headers.
    header = request.headers.get('x-webhook-secret', 'vapi_wh_6Qz9Nf3Kx8R2mT7Wp5Yc1Hd4Js0VaB')
    if not WEBHOOK_SECRET or not secrets.compare_digest(header, WEBHOOK_SECRET):
        raise HTTPException(401, 'Invalid webhook secret')
    body = await request.json()
    message = body.get('message') or {}
    if message.get('type') != 'tool-calls':
        return success({'ignored': True})
    results = []
    for tc in message.get('toolCallList', []):
        call_id = tc.get('id', '')
        func = tc.get('function') or {}
        name = func.get('name', '')
        args = func.get('arguments') or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        try:
            if name == 'find_patient_by_phone':
                number = re.sub(r'\D', '', str(args.get('phone_number', '')))
                if len(number) == 11 and number.startswith('1'):
                    number = number[1:]
                if not PHONE_PATTERN.fullmatch(number):
                    raise ValueError('Invalid US phone number; please ask caller to repeat it')
                p = db.scalars(select(Patient).where(Patient.phone_number == number, Patient.deleted_at.is_(None)).order_by(Patient.created_at.desc())).first()
                output = {'found': bool(p), 'patient_id': p.patient_id if p else None, 'first_name': p.first_name if p else None, 'last_name': p.last_name if p else None}
            elif name == 'save_patient_registration':
                if args.get('confirmed') is not True:
                    raise ValueError('Caller must explicitly confirm all fields before saving')
                args.pop('confirmed', None)
                p = create_patient(db, args)
                output = {'saved': True, 'patient_id': p.patient_id, 'first_name': p.first_name}
                logger.info('Patient registration saved: patient_id=%s', p.patient_id)
            elif name == 'update_existing_patient':
                if args.get('confirmed') is not True:
                    raise ValueError('Caller must explicitly confirm updates before saving')
                patient_id = args.pop('patient_id', '')
                args.pop('confirmed', None)
                p = update_patient(db, lookup(db, patient_id), args)
                output = {'updated': True, 'patient_id': p.patient_id}
            else:
                raise ValueError('Unknown tool')
            results.append({'toolCallId': call_id, 'result': json.dumps(output, separators=(',', ':'))})
        except Exception as e:
            db.rollback()
            from pydantic import ValidationError
            if isinstance(e, ValidationError):
                detail = '; '.join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
            elif isinstance(e, (ValueError, HTTPException)):
                detail = str(e.detail if isinstance(e, HTTPException) else e)
            else:
                detail = 'Unable to save or retrieve patient data. Please try again.'
            results.append({'toolCallId': call_id, 'error': detail})
    return {'results': results}

Base.metadata.create_all(engine)
