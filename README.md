# GAR Deploy Service (Render Edition)

A standard FastAPI service to deploy Docker images from a GitHub repository to Google Artifact Registry using **Google Cloud Build**. This architecture is compatible with PaaS providers like Render, Heroku, or Rail that do not support privileged Docker execution.

## How it Works

1.  **Receive**: POST request with a GitHub URL.
2.  **Clone & Zip**: Clones the repo locally and creates a zip archive.
3.  **Upload**: Uploads the zip to Google Cloud Storage (GCS).
4.  **Build**: Triggers Google Cloud Build to pull the zip from GCS and build/push the Docker image to GAR.

## Prerequisites

1.  **Google Cloud Project**:
    -   Enable **Cloud Build API**.
    -   Enable **Cloud Storage API**.
    -   Enable **Artifact Registry API**.
2.  **Service Account**:
    -   Create a Service Account.
    -   **Roles**:
        -   `Cloud Build Editor` (to trigger builds)
        -   `Storage Object Admin` (to upload source code)
        -   `Service Account User` (to act as the build service account)
    -   **Keys**: Generate a JSON key file.

## Setup on Render

1.  **Create New Web Service**: Connect your repo containing this code (not the repo you want to deploy, but *this* server code).
2.  **Environment Variables**:
    Add the following Environment Variables in the Render Dashboard:

    | Key | Value | Description |
    | :--- | :--- | :--- |
    | `GCP_PROJECT_ID` | `your-project-id` | Your Google Cloud Project ID |
    | `GCP_REGION` | `us-central1` | Region for GAR and Cloud Build |
    | `GAR_REPOSITORY_NAME` | `your-repo` | Name of the Artifact Registry repo |
    | `GCP_STORAGE_BUCKET` | `your-bucket` | (Optional) Staging bucket for source code. Defaults to `{project_id}_cloudbuild` |
    | `GOOGLE_APPLICATION_CREDENTIALS` | `/etc/secrets/google-credentials.json` | Path to the secret credential file (see below) |

3.  **Secret File**:
    In Render, create a "Secret File":
    -   **Filename**: `google-credentials.json`
    -   **Content**: Paste the *entire* content of your Service Account JSON key.

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Set local env vars
export GCP_PROJECT_ID="job-runner-prod"
# ...

# Run
uvicorn main:app --reload
```
