# Use a lightweight Python image
FROM python:3.11-slim

# Set the working directory
WORKDIR /app

# Install system dependencies needed for some Python packages
# Install only the bare essentials
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy the rest of your app code
COPY . .

# Expose the port Streamlit runs on
EXPOSE 8501

# Healthcheck to let AWS know the container is alive
HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health

# Run the app
ENTRYPOINT ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]


