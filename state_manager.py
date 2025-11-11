"""
State management for resumable video conversion jobs.
Stores job state in S3 for durability and resumability.
"""
import json
import os
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict
from pathlib import Path

import boto3
from pydantic import BaseModel, Field


class ChunkStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobStatus(str, Enum):
    INITIALIZED = "initialized"
    SPLITTING = "splitting"
    CONVERTING = "converting"
    COMBINING = "combining"
    COMPLETED = "completed"
    FAILED = "failed"


class ChunkState(BaseModel):
    chunk_id: int
    start_time: float
    end_time: float
    status: ChunkStatus = ChunkStatus.PENDING
    s3_input_key: Optional[str] = None
    s3_output_key: Optional[str] = None
    attempt_count: int = 0
    error_message: Optional[str] = None
    completed_at: Optional[str] = None


class JobState(BaseModel):
    job_id: str
    video_name: str
    status: JobStatus = JobStatus.INITIALIZED
    s3_input_key: str
    s3_output_key: Optional[str] = None
    total_duration: float
    num_chunks: int
    chunks: List[ChunkState] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    metadata: Dict = Field(default_factory=dict)


class StateManager:
    """Manages job state with S3 persistence for resumability."""

    def __init__(self, s3_bucket: str, state_prefix: str = "states/"):
        self.s3_bucket = s3_bucket
        self.state_prefix = state_prefix
        self.s3_client = boto3.client('s3')

    def _get_state_key(self, job_id: str) -> str:
        """Get S3 key for job state file."""
        return f"{self.state_prefix}{job_id}.state.json"

    def create_job(
        self,
        job_id: str,
        video_name: str,
        s3_input_key: str,
        total_duration: float,
        num_chunks: int
    ) -> JobState:
        """Create a new job state."""
        chunks = []
        if num_chunks == 1:
            # Single chunk - process entire video
            chunks.append(ChunkState(
                chunk_id=0,
                start_time=0,
                end_time=total_duration
            ))
        else:
            # Multiple chunks - split with overlap
            chunk_duration = total_duration / num_chunks
            overlap = float(os.getenv('CHUNK_OVERLAP_SECONDS', '2'))

            for i in range(num_chunks):
                start = max(0, i * chunk_duration - (overlap if i > 0 else 0))
                end = min(total_duration, (i + 1) * chunk_duration + overlap)

                chunks.append(ChunkState(
                    chunk_id=i,
                    start_time=start,
                    end_time=end
                ))

        job_state = JobState(
            job_id=job_id,
            video_name=video_name,
            s3_input_key=s3_input_key,
            total_duration=total_duration,
            num_chunks=num_chunks,
            chunks=chunks
        )

        self.save_state(job_state)
        return job_state

    def save_state(self, job_state: JobState):
        """Save job state to S3."""
        job_state.updated_at = datetime.utcnow().isoformat()
        state_key = self._get_state_key(job_state.job_id)

        self.s3_client.put_object(
            Bucket=self.s3_bucket,
            Key=state_key,
            Body=job_state.model_dump_json(indent=2),
            ContentType='application/json'
        )

    def load_state(self, job_id: str) -> Optional[JobState]:
        """Load job state from S3."""
        state_key = self._get_state_key(job_id)

        try:
            response = self.s3_client.get_object(
                Bucket=self.s3_bucket,
                Key=state_key
            )
            state_data = json.loads(response['Body'].read())
            return JobState(**state_data)
        except self.s3_client.exceptions.NoSuchKey:
            return None

    def update_chunk_status(
        self,
        job_state: JobState,
        chunk_id: int,
        status: ChunkStatus,
        s3_output_key: Optional[str] = None,
        error_message: Optional[str] = None
    ):
        """Update the status of a specific chunk."""
        chunk = job_state.chunks[chunk_id]
        chunk.status = status

        if s3_output_key:
            chunk.s3_output_key = s3_output_key

        if error_message:
            chunk.error_message = error_message

        if status == ChunkStatus.COMPLETED:
            chunk.completed_at = datetime.utcnow().isoformat()

        if status in [ChunkStatus.PROCESSING, ChunkStatus.FAILED]:
            chunk.attempt_count += 1

        # Update overall job status
        all_completed = all(c.status == ChunkStatus.COMPLETED for c in job_state.chunks)
        any_failed = any(c.status == ChunkStatus.FAILED for c in job_state.chunks)

        if all_completed and job_state.status == JobStatus.CONVERTING:
            job_state.status = JobStatus.COMBINING
        elif any_failed:
            job_state.status = JobStatus.FAILED

        self.save_state(job_state)

    def get_pending_chunks(self, job_state: JobState) -> List[ChunkState]:
        """Get all chunks that need processing (pending, processing, or failed with retries)."""
        max_retries = 3
        return [
            chunk for chunk in job_state.chunks
            if chunk.status == ChunkStatus.PENDING or
            chunk.status == ChunkStatus.PROCESSING or  # Include stuck processing chunks
            (chunk.status == ChunkStatus.FAILED and chunk.attempt_count < max_retries)
        ]

    def list_jobs(self, status_filter: Optional[JobStatus] = None) -> List[JobState]:
        """List all jobs, optionally filtered by status."""
        jobs = []

        paginator = self.s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=self.s3_bucket, Prefix=self.state_prefix):
            if 'Contents' not in page:
                continue

            for obj in page['Contents']:
                if obj['Key'].endswith('.state.json'):
                    job_id = Path(obj['Key']).stem.replace('.state', '')
                    job_state = self.load_state(job_id)

                    if job_state and (status_filter is None or job_state.status == status_filter):
                        jobs.append(job_state)

        return jobs
