"""
Main orchestration logic for cloud-based 3D video conversion.
Handles video analysis, splitting decisions, and Modal job coordination.
"""
import os
import subprocess
import json
from pathlib import Path
from typing import Optional
import uuid

import boto3
from tqdm import tqdm

from state_manager import StateManager, JobStatus, ChunkStatus


class VideoConverter:
    """Orchestrates cloud-based 3D video conversion with resumability."""

    def __init__(
        self,
        s3_bucket: str,
        max_gpus: int = 8,
        split_threshold_seconds: float = 480,  # 8 minutes
    ):
        self.s3_bucket = s3_bucket
        self.max_gpus = max_gpus
        self.split_threshold = split_threshold_seconds
        self.s3_client = boto3.client('s3')
        self.state_manager = StateManager(s3_bucket)

    def get_video_duration(self, video_path: str) -> float:
        """Get video duration in seconds using ffprobe."""
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'json',
            video_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        return float(data['format']['duration'])

    def upload_to_s3(self, local_path: str, s3_key: str) -> None:
        """Upload file to S3 with progress bar."""
        file_size = Path(local_path).stat().st_size

        with tqdm(total=file_size, unit='B', unit_scale=True, desc=f"Uploading {Path(local_path).name}") as pbar:
            self.s3_client.upload_file(
                local_path,
                self.s3_bucket,
                s3_key,
                Callback=lambda bytes_transferred: pbar.update(bytes_transferred)
            )

    def download_from_s3(self, s3_key: str, local_path: str) -> None:
        """Download file from S3 with progress bar."""
        # Get file size
        response = self.s3_client.head_object(Bucket=self.s3_bucket, Key=s3_key)
        file_size = response['ContentLength']

        with tqdm(total=file_size, unit='B', unit_scale=True, desc=f"Downloading {Path(s3_key).name}") as pbar:
            self.s3_client.download_file(
                self.s3_bucket,
                s3_key,
                local_path,
                Callback=lambda bytes_transferred: pbar.update(bytes_transferred)
            )

    def convert_video(
        self,
        input_video: str,
        output_video: Optional[str] = None,
        job_id: Optional[str] = None,
        model_config: Optional[dict] = None,
    ) -> str:
        """
        Convert video to 3D using cloud GPUs.

        Args:
            input_video: Path to input video file
            output_video: Optional path for output video (defaults to input_3d.mp4)
            job_id: Optional job ID to resume an existing job
            model_config: Optional iw3 model configuration

        Returns:
            Job ID for tracking/resuming
        """
        input_path = Path(input_video)
        if not input_path.exists():
            raise FileNotFoundError(f"Input video not found: {input_video}")

        if output_video is None:
            output_video = str(input_path.parent / f"{input_path.stem}_3d{input_path.suffix}")

        # Check if resuming existing job
        if job_id:
            print(f"Resuming job: {job_id}")
            job_state = self.state_manager.load_state(job_id)
            if not job_state:
                raise ValueError(f"Job not found: {job_id}")
        else:
            # New job - analyze video and create state
            job_id = str(uuid.uuid4())
            print(f"Starting new job: {job_id}")
            print(f"Analyzing video: {input_video}")

            duration = self.get_video_duration(str(input_path))
            print(f"Video duration: {duration:.2f} seconds")

            # Determine number of chunks
            if duration <= self.split_threshold:
                num_chunks = 1
                print(f"Video is short ({duration:.2f}s), processing as single chunk")
            else:
                num_chunks = min(self.max_gpus, int(duration / 60))  # ~1 minute per GPU
                print(f"Video is long ({duration:.2f}s), splitting into {num_chunks} chunks")

            # Upload input video to S3
            s3_input_key = f"inputs/{job_id}/{input_path.name}"
            print(f"Uploading to S3: s3://{self.s3_bucket}/{s3_input_key}")
            self.upload_to_s3(str(input_path), s3_input_key)

            # Create job state
            job_state = self.state_manager.create_job(
                job_id=job_id,
                video_name=input_path.name,
                s3_input_key=s3_input_key,
                total_duration=duration,
                num_chunks=num_chunks
            )
            job_state.status = JobStatus.CONVERTING
            job_state.metadata['output_path'] = output_video
            if model_config:
                job_state.metadata['model_config'] = model_config
            self.state_manager.save_state(job_state)

        # Process chunks using Modal
        self._process_chunks(job_state, model_config)

        # Combine chunks if needed
        if job_state.num_chunks > 1:
            self._combine_chunks(job_state)

        # Download final result
        self._download_result(job_state, output_video)

        print(f"\n✓ Conversion complete: {output_video}")
        print(f"  Job ID: {job_id}")

        return job_id

    def _process_chunks(self, job_state, model_config: Optional[dict]):
        """Process all pending chunks using Modal."""
        import modal

        pending_chunks = self.state_manager.get_pending_chunks(job_state)

        if not pending_chunks:
            print("No pending chunks to process")
            return

        print(f"\nProcessing {len(pending_chunks)} chunks on Modal...")

        # Lookup deployed Modal function
        import modal
        convert_video_chunk = modal.Function.lookup("video-3d-converter", "convert_video_chunk")

        # Process chunks in parallel
        results = []
        for chunk in pending_chunks:
            # Mark as processing
            self.state_manager.update_chunk_status(
                job_state,
                chunk.chunk_id,
                ChunkStatus.PROCESSING
            )

            # Generate output key
            s3_output_key = f"outputs/{job_state.job_id}/chunk_{chunk.chunk_id:03d}.mp4"

            # Spawn Modal function
            result = convert_video_chunk.spawn(
                s3_bucket=self.s3_bucket,
                s3_input_key=job_state.s3_input_key,
                s3_output_key=s3_output_key,
                start_time=chunk.start_time,
                end_time=chunk.end_time,
                chunk_id=chunk.chunk_id,
                job_id=job_state.job_id,
                model_config=model_config or {},
            )
            results.append((chunk.chunk_id, result))

        # Wait for all chunks to complete with progress bar
        with tqdm(total=len(results), desc="Converting chunks") as pbar:
            for chunk_id, future in results:
                try:
                    result = future.get()

                    if result['status'] == 'success':
                        self.state_manager.update_chunk_status(
                            job_state,
                            chunk_id,
                            ChunkStatus.COMPLETED,
                            s3_output_key=result['s3_output_key']
                        )
                        pbar.update(1)
                    else:
                        self.state_manager.update_chunk_status(
                            job_state,
                            chunk_id,
                            ChunkStatus.FAILED,
                            error_message=result.get('error', 'Unknown error')
                        )
                        print(f"\n✗ Chunk {chunk_id} failed: {result.get('error', 'Unknown error')}")

                except Exception as e:
                    self.state_manager.update_chunk_status(
                        job_state,
                        chunk_id,
                        ChunkStatus.FAILED,
                        error_message=str(e)
                    )
                    print(f"\n✗ Chunk {chunk_id} failed with exception: {e}")

        # Check if any chunks failed
        failed_chunks = [c for c in job_state.chunks if c.status == ChunkStatus.FAILED]
        if failed_chunks:
            raise RuntimeError(f"{len(failed_chunks)} chunks failed. Job can be resumed with: --resume {job_state.job_id}")

    def _combine_chunks(self, job_state):
        """Combine chunks using Modal (or locally if preferred)."""
        print("\nCombining chunks...")

        if job_state.num_chunks == 1:
            # Single chunk - just use it as output
            job_state.s3_output_key = job_state.chunks[0].s3_output_key
            job_state.status = JobStatus.COMPLETED
            self.state_manager.save_state(job_state)
            return

        import modal
        combine_video_chunks = modal.Function.lookup("video-3d-converter", "combine_video_chunks")

        # Get chunk keys in order
        chunk_keys = [chunk.s3_output_key for chunk in sorted(job_state.chunks, key=lambda c: c.chunk_id)]

        output_key = f"outputs/{job_state.job_id}/final.mp4"

        # Run combining on Modal
        result = combine_video_chunks.remote(
            s3_bucket=self.s3_bucket,
            chunk_keys=chunk_keys,
            output_key=output_key,
            job_id=job_state.job_id,
        )

        if result['status'] == 'success':
            job_state.s3_output_key = output_key
            job_state.status = JobStatus.COMPLETED
            self.state_manager.save_state(job_state)
            print("✓ Chunks combined successfully")
        else:
            raise RuntimeError(f"Failed to combine chunks: {result.get('error')}")

    def _download_result(self, job_state, output_path: str):
        """Download final result from S3."""
        print(f"\nDownloading result...")

        if not job_state.s3_output_key:
            raise RuntimeError("No output key found in job state")

        self.download_from_s3(job_state.s3_output_key, output_path)

    def list_jobs(self, status_filter: Optional[JobStatus] = None):
        """List all jobs with optional status filter."""
        jobs = self.state_manager.list_jobs(status_filter)

        if not jobs:
            print("No jobs found")
            return

        print(f"\n{'Job ID':<36} {'Status':<12} {'Video':<30} {'Chunks':<8} {'Created'}")
        print("-" * 110)

        for job in jobs:
            completed = sum(1 for c in job.chunks if c.status == ChunkStatus.COMPLETED)
            chunks_str = f"{completed}/{job.num_chunks}"
            print(f"{job.job_id:<36} {job.status.value:<12} {job.video_name:<30} {chunks_str:<8} {job.created_at[:19]}")
