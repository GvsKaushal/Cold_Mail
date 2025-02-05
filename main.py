from fastapi import FastAPI, Request, Depends, status, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel
from typing import List
from passlib.context import CryptContext
from fastapi_login import LoginManager
import os
from dotenv import load_dotenv
from datetime import timedelta
from bson import ObjectId
from langchain_community.document_loaders import WebBaseLoader
from pymongo.errors import PyMongoError

from chains import Chain
from portfolio import Portfolio
from utils import clean_text

# Load environment variables
load_dotenv()
SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY:
    raise ValueError("SECRET_KEY is not set. Check your .env file.")

# Constants
MONGO_DETAILS = "mongodb://localhost:27017/"
ACCESS_TOKEN_EXPIRES_MINUTES = 60

# Database setup
client = AsyncIOMotorClient(MONGO_DETAILS)
db = client["user_info"]
user_collection = db["users"]

# Password context
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_hashed_password(plain_password):
    return pwd_ctx.hash(plain_password)


def verify_password(plain_password, hashed_password):
    return pwd_ctx.verify(plain_password, hashed_password)


# User models
class portfolio(BaseModel):
    Techstack: str
    Links: str


class User(BaseModel):
    username: str
    name: str
    position: str
    company: str
    portfolio: List[portfolio]


class UserDB(User):
    hashed_password: str


# FastAPI app setup
app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Login manager
manager = LoginManager(secret=SECRET_KEY, token_url="/login", use_cookie=True)
manager.cookie_name = "auth"


@manager.user_loader()
async def get_user_from_db(username: str):
    user = await user_collection.find_one({"username": username})
    if not user:
        return None
    return user


async def authenticate_user(username: str, password: str):
    user = await get_user_from_db(username)
    if not user or not verify_password(password, user["hashed_password"]):
        return None
    return user


# Exception handling
class NotAuthenticatedException(Exception):
    pass


def not_authenticated_exception_handler(request, exception):
    return RedirectResponse("/login")


manager._not_authenticated_exception = NotAuthenticatedException
app.add_exception_handler(NotAuthenticatedException, not_authenticated_exception_handler)


# Routes
@app.get("/", response_class=HTMLResponse)
def root(request: Request, user: User = Depends(manager)):
    return templates.TemplateResponse("index.html", {"request": request, "title": "Home", "user": user})


@app.get("/login", response_class=HTMLResponse)
def get_login(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "title": "Login"})


@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    user = await authenticate_user(username, password)
    if not user:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "title": "Login", "invalid": True},
            status_code=status.HTTP_401_UNAUTHORIZED
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRES_MINUTES)
    access_token = manager.create_access_token(
        data={"sub": user["username"]},
        expires=access_token_expires
    )
    resp = RedirectResponse("/home", status_code=status.HTTP_302_FOUND)
    manager.set_cookie(resp, access_token)
    return resp


@app.get("/home")
async def home(request: Request, user: User = Depends(manager)):  # Authenticated route
    return templates.TemplateResponse(
        "home.html", {"request": request, "title": "Home", "user": user}
    )


@app.get("/logout", response_class=RedirectResponse)
def logout():
    response = RedirectResponse("/")
    manager.set_cookie(response, None)
    return response


@app.get("/register", response_class=HTMLResponse)
def get_register(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "title": "Register"})


@app.post("/register")
async def register(
        request: Request,
        username: str = Form(...),
        name: str = Form(...),
        position: str = Form(...),
        company: str = Form(...),
        password: str = Form(...)
):
    form_data = await request.form()
    portfolios = []

    # Parse portfolio data correctly
    current_portfolio = {}
    for key, value in form_data.multi_items():
        if key.startswith("portfolios") and "Techstack" in key:
            current_portfolio["Techstack"] = value
        elif key.startswith("portfolios") and "Links" in key:
            current_portfolio["Links"] = value

        # Add the portfolio once both fields are captured
        if "Techstack" in current_portfolio and "Links" in current_portfolio:
            portfolios.append(current_portfolio)
            current_portfolio = {}  # Reset for the next portfolio

    # Check for existing user
    existing_user = await user_collection.find_one({"username": username})
    if existing_user:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "title": "Register", "error": "User already exists"},
            status_code=status.HTTP_400_BAD_REQUEST
        )

    # Hash password and create user data
    hashed_password = get_hashed_password(password)
    user_data = {
        "username": username,
        "name": name,
        "position": position,
        "company": company,
        "hashed_password": hashed_password,
        "portfolio": portfolios
    }

    # Insert user_data into the database
    await user_collection.insert_one(user_data)
    response = RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    return response


# Initialize required objects
chain = Chain()
portfolioo = Portfolio()


class URLInput(BaseModel):
    url: str


@app.post("/generate-email/")
async def generate_email(input_data: URLInput, user: User = Depends(manager)):
    try:
        loader = WebBaseLoader([input_data.url])
        pages = loader.load()
        if not pages:
            raise ValueError("No data found at the provided URL.")
        data = clean_text(pages.pop().page_content)
        portfolioo.load_portfolio()
        jobs = chain.extract_jobs(data)
        emails = []

        db2 = client["user_info"]
        job_collection = db2["job_applications"]

        name = user["name"]
        position = user["position"]
        company = user["company"]

        for job in jobs:
            skills = job.get('skills', [])
            links = portfolioo.query_links(skills)
            email = chain.write_mail(job, links, name, position, company)
            emails.append({"job": job, "email": email})

            job_entry = {
                "username": user["username"],
                "URL": input_data.url,
                "role": job.get("role"),
                "email": email,
                "status": "Draft"
            }

            await job_collection.insert_one(job_entry)

        return {"emails": emails}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")


@app.get("/job-tracker", response_class=HTMLResponse)
async def job_tracker_route(request: Request, user: User = Depends(manager)):
    if not user:
        return RedirectResponse("/login")

    try:
        username = user["username"]

        db3 = client["user_info"]
        job_collection = db3["job_applications"]

        jobs = await job_collection.find({"username": username}).to_list(100)

        if not jobs:
            return templates.TemplateResponse(
                "job_tracker.html",
                {"request": request, "title": "Job Tracker", "message": "No job applications found. Start tracking!",
                 "user": user},
            )

        return templates.TemplateResponse(
            "job_tracker.html",
            {"request": request, "title": "Job Tracker", "jobs": jobs, "user": user},
        )

    except PyMongoError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")


@app.post("/update-status/{job_id}")
async def update_status(job_id: str, status: str = Form(...)):
    try:
        # Convert job_id to ObjectId
        try:
            job_id = ObjectId(job_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid job_id format")

        # Validate status
        valid_statuses = ["Draft", "Applied", "Interview", "Offered", "Rejected", "Accepted"]
        if status not in valid_statuses:
            raise HTTPException(status_code=400, detail="Invalid status value")

        # Connect to job_applications collection
        db3 = client["user_info"]
        job_collection = db3["job_applications"]

        # Check if the job exists
        job = await job_collection.find_one({"_id": job_id})
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Update the job status
        result = await job_collection.update_one({"_id": job_id}, {"$set": {"status": status}})
        if result.modified_count == 0:
            raise HTTPException(status_code=500, detail="Failed to update the job status")

    except HTTPException:
        raise  # Re-raise specific exceptions
    except Exception as e:
        # Log the exception and return an error response
        print(f"Error updating job status: {e}")
        raise HTTPException(status_code=500, detail="An error occurred while updating the job status")
