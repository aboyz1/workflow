import os
import shutil
import uuid
import logging
import zipfile
from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel, HttpUrl
import git
from google.cloud.devtools import cloudbuild_v1
from google.cloud import storage
from config import settings

app = FastAPI()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DeployRequest(BaseModel):
    github_url: HttpUrl

def zip_directory(folder_path, output_path):
    """Zips the contents of a directory."""
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, folder_path)
                zipf.write(file_path, arcname)

def build_and_push_task(github_url: str, request_id: str):
    temp_dir = f"temp_build_{request_id}"
    archive_path = f"{temp_dir}.zip"
    
    logger.info(f"Starting build task for {github_url} with ID {request_id}")
    
    try:
        # 1. Clone Repository
        os.makedirs(temp_dir, exist_ok=True)
        logger.info(f"Cloning {github_url} into {temp_dir}")
        git.Repo.clone_from(github_url, temp_dir)
        
        # 2. Check for Dockerfile (optional sanity check before uploading)
        if not os.path.exists(os.path.join(temp_dir, "Dockerfile")):
             logger.error("No Dockerfile found.")
             return

        # 3. Zip Repository
        logger.info("Zipping repository...")
        zip_directory(temp_dir, archive_path)
        
        # 4. Upload to GCS
        storage_client = storage.Client(project=settings.gcp_project_id)
        bucket_name = settings.gcp_storage_bucket

        
        # Ensure bucket exists (best effort)
        try:
             bucket = storage_client.get_bucket(bucket_name)
        except Exception:
             logger.info(f"Bucket {bucket_name} not found, using default staging bucket logic from Cloud Build or erroring if not exists.")
             # Fallback: Let Cloud Build handle it or assume user provided a valid bucket
             # For simplicity, we assume the bucket exists or we create it.
             # In production, infrastructure should provision this.
             bucket = storage_client.bucket(bucket_name)

        blob_name = f"source/{request_id}.zip"
        blob = bucket.blob(blob_name)
        logger.info(f"Uploading source to gs://{bucket_name}/{blob_name}")
        blob.upload_from_filename(archive_path)

        # 5. Trigger Cloud Build
        build_client = cloudbuild_v1.CloudBuildClient()
        
        repo_name = github_url.split("/")[-1].replace(".git", "")
        # {REGION}-docker.pkg.dev/{PROJECT_ID}/{REPOSITORY}/{IMAGE_NAME}:{TAG}
        image_tag = f"{settings.gcp_region}-docker.pkg.dev/{settings.gcp_project_id}/{settings.gar_repository_name}/{repo_name}:{request_id}"
        
        build = cloudbuild_v1.Build()
        
        # Define the build steps
        # Equivalent to: docker build -t {image_tag} . && docker push {image_tag}
        build.steps = [
            {
                "name": "gcr.io/cloud-builders/docker",
                "args": ["build", "-t", image_tag, "."]
            },
            {
                "name": "gcr.io/cloud-builders/docker",
                "args": ["push", image_tag]
            }
        ]
        
        build.source = {
            "storage_source": {
                "bucket": bucket_name,
                "object_": blob_name
            }
        }
        
        build.images = [image_tag]
        build.projectId = settings.gcp_project_id
        
        logger.info("Triggering Cloud Build...")
        operation = build_client.create_build(project_id=settings.gcp_project_id, build=build)
        
        # We catch the result locally for logging, but this blocks the thread if we .result(). 
        # Since this is run in BackgroundTasks, blocking here is okay-ish for a worker, 
        # but to be truly async we might just fire and forget or update a database status.
        # For this requirement, we'll wait for the trigger response (not the full build)
        
        logger.info(f"Cloud Build triggered: {operation.metadata.build.id}")
        logger.info(f"Build logs: {operation.metadata.build.log_url}")

    except Exception as e:
        logger.error(f"Build failed: {e}")
    finally:
        # Cleanup local files
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        if os.path.exists(archive_path):
            os.remove(archive_path)

@app.post("/deploy")
async def deploy(request: DeployRequest, background_tasks: BackgroundTasks):
    request_id = str(uuid.uuid4())
    background_tasks.add_task(build_and_push_task, str(request.github_url), request_id)
    
    return {
        "message": "Deployment started via Cloud Build",
        "request_id": request_id,
        "repo": str(request.github_url)
    }
