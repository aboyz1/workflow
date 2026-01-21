import os
import shutil
import uuid
import logging
import zipfile
import threading
import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import git
from google.cloud.devtools import cloudbuild_v1
from google.cloud import storage
from google.cloud import firestore
from config import settings

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": [
    "https://studio.firebase.google.com",
    "https://dev--perd-fd33f.europe-west4.hosted.app",
    "https://6000-firebase-studio-1753801228661.cluster-lu4mup47g5gm4rtyvhzpwbfadi.cloudworkstations",
    "https://6000-firebase-studio-1753801228661.cluster-lu4mup47g5gm4rtyvhzpwbfadi.cloudworkstations.dev/dashboard/workflows"
]}})

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

def update_firestore_status(request_id, status, metadata=None):
    """Helper to update Firestore status."""
    try:
        db = firestore.Client(project=settings.gcp_project_id)
        doc_ref = db.collection("deployments").document(request_id)
        
        data = {
            "status": status,
            "timestamp": datetime.datetime.now(datetime.timezone.utc)
        }
        if metadata:
            data.update(metadata)
            
        doc_ref.set(data, merge=True)
        logger.info(f"Updated Firestore {request_id} to {status}")
    except Exception as e:
        logger.error(f"Failed to update Firestore for {request_id}: {e}")

def build_and_push_task(github_url: str, request_id: str, workflow_name: str, user_id: str):


    temp_dir = f"temp_build_{request_id}"
    archive_path = f"{temp_dir}.zip"
    
    logger.info(f"Starting build task for {github_url} with ID {request_id}")
    
    # 1. Update status to IN_PROGRESS
    update_firestore_status(request_id, "IN_PROGRESS", {
        "workflow_name": workflow_name,
        "user_id": user_id
    })



    try:
        # 2. Clone Repository
        os.makedirs(temp_dir, exist_ok=True)
        logger.info(f"Cloning {github_url} into {temp_dir}")
        git.Repo.clone_from(github_url, temp_dir)
        
        # 3. Check for Dockerfile
        if not os.path.exists(os.path.join(temp_dir, "Dockerfile")):
             logger.error("No Dockerfile found.")
             update_firestore_status(request_id, "FAILURE", {"error": "No Dockerfile found"})
             return

        # 4. Zip Repository
        logger.info("Zipping repository...")
        zip_directory(temp_dir, archive_path)
        
        # 5. Upload to GCS
        storage_client = storage.Client(project=settings.gcp_project_id)
        bucket_name = settings.gcp_storage_bucket
        
        try:
             bucket = storage_client.get_bucket(bucket_name)
        except Exception:
             logger.info(f"Bucket {bucket_name} not found, using default staging bucket logic.")
             bucket = storage_client.bucket(bucket_name)

        blob_name = f"source/{request_id}.zip"
        blob = bucket.blob(blob_name)
        logger.info(f"Uploading source to gs://{bucket_name}/{blob_name}")
        blob.upload_from_filename(archive_path)

        # 6. Trigger Cloud Build
        build_client = cloudbuild_v1.CloudBuildClient()
        
        repo_name = github_url.split("/")[-1].replace(".git", "")
        image_tag = f"{settings.gcp_region}-docker.pkg.dev/{settings.gcp_project_id}/{settings.gar_repository_name}/{repo_name}:{request_id}"
        
        build = cloudbuild_v1.Build()
        
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
        
        # Wait for completion
        logger.info("Waiting for build to complete...")
        result = operation.result() 
        logger.info(f"Build finished status: {result.status}")

        if result.status == cloudbuild_v1.Build.Status.SUCCESS:
             update_firestore_status(request_id, "SUCCESS", {
                 "image_tag": image_tag,
                 "build_id": operation.metadata.build.id
             })
        else:
             update_firestore_status(request_id, "FAILURE", {
                 "build_id": operation.metadata.build.id,
                 "error": f"Cloud Build failed with status: {result.status}"
             })
        
        # Cleanup GCS blob
        try:
            blob.delete()
        except Exception as e:
            logger.warning(f"Failed to delete source blob {blob_name}: {e}")

    except Exception as e:
        logger.error(f"Build failed: {e}")
        update_firestore_status(request_id, "FAILURE", {"error": str(e)})
    finally:
        # Cleanup local files
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
    workflow_name = data.get('workflow_name', 'unnamed')
    user_id = data.get('userId', 'anonymous')
    request_id = str(uuid.uuid4())
    
    # 1. Initial write to Firestore
    update_firestore_status(request_id, "PENDING", {
        "request_id": request_id,
        "github_url": github_url,
        "workflow_name": workflow_name,
        "user_id": user_id
    })
    
    # 2. Start background thread
    thread = threading.Thread(target=build_and_push_task, args=(github_url, request_id, workflow_name, user_id))
    thread.start()


    
    return jsonify({
        "message": "Deployment started",
        "request_id": request_id,
        "status_url": f"/status/{request_id}"
    })

@app.route("/status/<request_id>", methods=["GET"])
def check_status(request_id):
    try:
        db = firestore.Client(project=settings.gcp_project_id)
        doc_ref = db.collection("deployments").document(request_id)
        doc = doc_ref.get()
        
        if doc.exists:
            data = doc.to_dict()
            return jsonify({
                "status": data.get("status"),
                "request_id": request_id
            })

        else:
            return jsonify({"error": "Deployment not found"}), 404
    except Exception as e:
        logger.error(f"Failed to fetch status: {e}")
        return jsonify({"error": "Internal server error"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
