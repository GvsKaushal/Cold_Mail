from fastapi import FastAPI, Request, Depends, status, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from motor.motor_asyncio import AsyncIOMotorClient
from contextlib import asynccontextmanager
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
import time
from chains import Chain
from portfolio import Portfolio
from utils import clean_text
import redis
import json

# Connect to Redis
redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)

load_dotenv()
SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY:
    raise ValueError("SECRET_KEY is not set. Check your .env file.")

MONGO_DETAILS = os.getenv('Mongodb_connection')
ACCESS_TOKEN_EXPIRES_MINUTES = 60

mongo_client = AsyncIOMotorClient(MONGO_DETAILS)
db = mongo_client.get_database("user_info")
users = db.get_collection("users")
job_applications = db.get_collection("job_applications")


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Redis connection established")
    print("MongoDB connection established")

    yield  # Run the application

    if redis_client:
        redis_client.close()
        print("Redis connection closed")

    # Close MongoDB client on shutdown
    if mongo_client:
        mongo_client.close()
        print("MongoDB connection closed")


app = FastAPI(lifespan=lifespan)

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_hashed_password(plain_password):
    return pwd_ctx.hash(plain_password)


def verify_password(plain_password, hashed_password):
    return pwd_ctx.verify(plain_password, hashed_password)


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


templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

manager = LoginManager(secret=SECRET_KEY, token_url="/login", use_cookie=True)
manager.cookie_name = "auth"


@manager.user_loader()
async def get_user_from_db(username: str):
    user = await users.find_one({"username": username})

    if not user:
        return None
    return user


async def authenticate_user(username: str, password: str):
    user = await get_user_from_db(username)
    if not user or not verify_password(password, user["hashed_password"]):
        return None
    return user


class NotAuthenticatedException(Exception):
    pass


def not_authenticated_exception_handler(request, exception):
    return RedirectResponse("/login")


manager._not_authenticated_exception = NotAuthenticatedException
app.add_exception_handler(NotAuthenticatedException, not_authenticated_exception_handler)


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
async def home(request: Request, user: User = Depends(manager)):
    return templates.TemplateResponse(
        "home.html", {"request": request, "title": "Home", "user": user}
    )


@app.get("/logout", response_class=RedirectResponse)
def logout():
    response = RedirectResponse("/")
    manager.set_cookie(response, "")
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

    existing_user = await users.find_one({"username": username})
    if existing_user:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "title": "Register", "error": "User already exists"},
            status_code=status.HTTP_400_BAD_REQUEST
        )

    portfolios = []
    links = []

    current_portfolio = {}
    for key, value in form_data.multi_items():
        if key.startswith("portfolios") and "Techstack" in key:
            current_portfolio["Techstack"] = value
        elif key.startswith("portfolios") and "Links" in key:
            current_portfolio["Links"] = value

        if "Techstack" in current_portfolio and "Links" in current_portfolio:
            portfolios.append(current_portfolio)
            current_portfolio = {}

    hashed_password = get_hashed_password(password)
    user_data = {
        "username": username,
        "name": name,
        "position": position,
        "company": company,
        "hashed_password": hashed_password,
        "portfolio": portfolios,
        "links": links
    }

    await users.insert_one(user_data)

    response = RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    return response


chain = Chain()
portfolioo = Portfolio()


class URLInput(BaseModel):
    url: str


@app.post("/generate-email/")
async def generate_email(input_data: URLInput, user: User = Depends(manager)):
    start_time = time.time()
    try:

        generated_links = user["links"]

        if input_data.url in generated_links:
            already_exists = "Job Already Exists check Job Tracker"
            return {"already_exists": already_exists}

        cache_key = f"{input_data.url}"
        cached_result = redis_client.get(cache_key)

        if cached_result:
            print("Cache hit - returning cached result")
            jobs = eval(cached_result)
        else:
            loader = WebBaseLoader([input_data.url])
            pages = loader.load()
            if not pages:
                raise ValueError("No data found at the provided URL.")
            data = clean_text(pages.pop().page_content)
            jobs = chain.extract_jobs(data)

        portfolioo.load_portfolio(user)
        emails = []

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

            await job_applications.insert_one(job_entry)

        generated_links.append(input_data.url)

        await users.update_one(
            {"username": user["username"]},
            {"$set": {"links": generated_links}}
        )

        redis_client.set(cache_key, str(jobs), ex=300)

        username = user["username"]
        jobs_cache_key = f"jobs:{username}"
        redis_client.delete(jobs_cache_key)

        end_time = time.time()
        duration = end_time - start_time

        return {"emails": emails, "duration": duration}
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
        cache_key = f"jobs:{username}"

        cached_jobs = redis_client.get(cache_key)

        if cached_jobs:
            jobs = json.loads(cached_jobs)
            print("Cache hit: Retrieved jobs from Redis")
        else:
            # Retrieve all jobs in descending order
            jobs_cursor = job_applications.find({"username": username}).sort("_id", -1)
            jobs = [job async for job in jobs_cursor]

            # Convert ObjectId to string
            for job in jobs:
                job["_id"] = str(job["_id"])

            # Store jobs in Redis
            redis_client.set(cache_key, json.dumps(jobs), ex=600)  # Cache for 10 minutes
            print("Cache miss: Retrieved jobs from MongoDB and stored in Redis")

        # Calculate pagination data for the frontend
        total_jobs = len(jobs)
        limit = 2  # Default limit per page
        total_pages = (total_jobs + limit - 1) // limit

        return templates.TemplateResponse(
            "job_tracker.html",
            {
                "request": request,
                "title": "Job Tracker",
                "jobs": jobs,
                "user": user["username"],
                "total_pages": total_pages,
                "limit": limit,
            },
        )

    except PyMongoError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")


@app.post("/update-status/{job_id}")
async def update_status(job_id: str, status: str = Form(...), user: User = Depends(manager)):
    try:
        username = user["username"]
        jobs_cache_key = f"jobs:{username}"
        redis_client.delete(jobs_cache_key)

        job_id = ObjectId(job_id)

        result = await job_applications.update_one({"_id": job_id}, {"$set": {"status": status}})

        if result.modified_count == 0:
            raise HTTPException(status_code=500, detail="Failed to update the job status")

    except Exception as e:
        print(f"Error updating job status: {e}")
        raise HTTPException(status_code=500, detail="An error occurred while updating the job status")


@app.get("/edit-user", response_class=HTMLResponse)
async def edit_user(request: Request, user: User = Depends(manager)):
    if not user:
        return RedirectResponse("/login")

    user_data = {
        "username": user["username"],
        "name": user["name"],
        "position": user["position"],
        "company": user["company"],
        "portfolio": user.get("portfolio", [])
    }

    return templates.TemplateResponse("edit_user.html",
                                      {"request": request, "title": "Edit Profile", "user": user_data})


@app.post("/edit-user")
async def update_user_details(
        request: Request,
        username: str = Form(...),
        name: str = Form(...),
        position: str = Form(...),
        company: str = Form(...),
):
    form_data = await request.form()
    portfolios = []
    current_portfolio = {}

    for key, value in form_data.multi_items():
        if key.startswith("portfolios") and "Techstack" in key:
            current_portfolio["Techstack"] = value
        elif key.startswith("portfolios") and "Links" in key:
            current_portfolio["Links"] = value

        if "Techstack" in current_portfolio and "Links" in current_portfolio:
            portfolios.append(current_portfolio)
            current_portfolio = {}

    update_data = {
        "name": name,
        "position": position,
        "company": company,
        "portfolio": portfolios
    }

    result = await users.update_one({"username": username}, {"$set": update_data})

    if result.modified_count == 0:
        return templates.TemplateResponse(
            "edit_user.html",
            {"request": request, "title": "Edit Profile", "error": "No changes detected"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return RedirectResponse("/home", status_code=status.HTTP_302_FOUND)


@app.delete("/delete-job/{job_id}")
async def delete_job(job_id: str, user: User = Depends(manager)):
    try:
        username = user["username"]
        generated_links = user["links"]

        job_id = ObjectId(job_id)

        job = await job_applications.find_one({"_id": job_id})
        url = job["URL"]

        if url in generated_links:
            generated_links.remove(url)

        await users.update_one(
            {"username": user["username"]},
            {"$set": {"links": generated_links}}
        )

        result = await job_applications.delete_one({"_id": job_id, "username": username})

        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Job not found or unauthorized")

        jobs_cache_key = f"jobs:{username}"
        redis_client.delete(jobs_cache_key)

        return {"detail": "Job deleted successfully"}

    except Exception as e:
        print(f"Error deleting job: {e}")
        raise HTTPException(status_code=500, detail="An error occurred while deleting the job")
