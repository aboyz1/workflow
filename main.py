import os
import shutil
import uuid
import logging
import zipfile
import threading
from flask import Flask, request, jsonify
import git
from google.cloud.devtools import cloudbuild_v1
from google.cloud import storage
from config import settings

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
        
        # 2. Check for Dockerfile
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
             logger.info(f"Bucket {bucket_name} not found, using default staging bucket logic.")
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
        
        logger.info("Triggering Cloud Build...")
        operation = build_client.create_build(project_id=settings.gcp_project_id, build=build)
        
        logger.info(f"Cloud Build triggered: {operation.metadata.build.id}")
        logger.info(f"Build logs: {operation.metadata.build.log_url}")

        # Wait for the build to complete to ensure we can safely delete the source
        logger.info("Waiting for build to complete...")
        result = operation.result() 
        logger.info(f"Build finished status: {result.status}")
        
        # Cleanup GCS blob
        try:
            logger.info(f"Deleting source blob: {blob_name}")
            blob.delete()
        except Exception as e:
            logger.warning(f"Failed to delete source blob {blob_name}: {e}")

    except Exception as e:
        logger.error(f"Build failed: {e}")
    finally:
        # Cleanup
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        if os.path.exists(archive_path):
            os.remove(archive_path)

@app.route("/deploy", methods=["POST"])
def deploy():
    data = request.get_json()
    if not data or 'github_url' not in data:
        return jsonify({"error": "Missing github_url"}), 400

    github_url = data['github_url']
    request_id = str(uuid.uuid4())
    
    # Run the build task in a separate thread
    thread = threading.Thread(target=build_and_push_task, args=(github_url, request_id))
    thread.start()
    
    return jsonify({
        "message": "Deployment started via Cloud Build",
        "request_id": request_id,
        "repo": github_url
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
