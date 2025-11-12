"""
Modal app for cloud-based 3D video conversion using iw3.
Supports resumable processing with B200 GPUs.
"""
import os
import tempfile
from pathlib import Path

import modal

# Create Modal app
app = modal.App("video-3d-converter")

# Create Modal image with iw3 and dependencies
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(
        "ffmpeg",
        "libsm6",
        "libxext6",
        "libxrender1",
        "libgomp1",
        "git",
    )
    .pip_install(
        "boto3",
        "ffmpeg-python",
    )
    # Install PyTorch with CUDA support
    .pip_install(
        "torch>=2.0.0",
        "torchvision>=0.15.0",
        index_url="https://download.pytorch.org/whl/cu121",
    )
    # Clone and install nunif/iw3
    .run_commands(
        "cd /root && git clone https://github.com/nagadomi/nunif.git",
        "cd /root/nunif && pip install -r requirements.txt",
        "cd /root/nunif && pip install -r requirements-torch.txt || true",
    )
    # Set working directory for iw3
    .env({"PYTHONPATH": "/root/nunif"})
)

# Create S3 secret for Modal
# Run: modal secret create aws-s3 AWS_ACCESS_KEY_ID=xxx AWS_SECRET_ACCESS_KEY=yyy AWS_REGION=us-east-1
s3_secret = modal.Secret.from_name("aws-s3")


@app.function(
    image=image,
    gpu="B200",  # or "H100", "A100" depending on availability
    secrets=[s3_secret],
    timeout=3600,  # 1 hour timeout per chunk
    retries=2,
)
def convert_video_chunk(
    s3_bucket: str,
    s3_input_key: str,
    s3_output_key: str,
    start_time: float,
    end_time: float,
    chunk_id: int,
    job_id: str,
    model_config: dict = None,
) -> dict:
    """
    Convert a video chunk to 3D using iw3.

    Args:
        s3_bucket: S3 bucket name
        s3_input_key: S3 key for input video
        s3_output_key: S3 key for output video
        start_time: Start time in seconds
        end_time: End time in seconds
        chunk_id: Chunk identifier
        job_id: Job identifier
        model_config: Optional iw3 model configuration

    Returns:
        Dict with status and metadata
    """
    import boto3
    import subprocess
    import traceback

    s3_client = boto3.client('s3')

    try:
        # Create temp directory for processing
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            input_path = tmpdir_path / "input.mp4"
            chunk_path = tmpdir_path / "chunk.mp4"
            output_path = tmpdir_path / "output.mp4"

            print(f"[Chunk {chunk_id}] Downloading from S3: {s3_input_key}")
            s3_client.download_file(s3_bucket, s3_input_key, str(input_path))

            # Extract chunk using ffmpeg
            duration = end_time - start_time
            print(f"[Chunk {chunk_id}] Extracting chunk: {start_time}s to {end_time}s ({duration}s)")

            extract_cmd = [
                "ffmpeg", "-y",
                "-ss", str(start_time),
                "-i", str(input_path),
                "-t", str(duration),
                "-c", "copy",
                str(chunk_path)
            ]
            subprocess.run(extract_cmd, check=True, capture_output=True)

            print(f"[Chunk {chunk_id}] Starting 3D conversion with iw3 (VDA_L model)")

            # Run iw3 conversion using Video Depth Anything Large model
            # Custom settings optimized for quality
            # Output format: Full Side-by-Side (SBS) - default when no format flag specified

            iw3_cmd = [
                "python", "-m", "iw3",
                "--input", str(chunk_path),
                "--output", str(output_path),
                "--depth-model", "VDA_L",  # Video Depth Anything Large model
                "--method", "mlbw_l2",  # MLBW L2 warping method
                "--divergence", "4.0",  # 3D strength
                "--convergence", "0.0",  # Convergence plane
                "--ipd-offset", "0.0",  # Your own size
                "--zoed-height", "1036",  # Depth resolution
                "--foreground-scale", "-0.5",  # Foreground scale
                "--edge-dilation", "2",  # Edge fix
                "--depth-aa",  # Depth antialiasing
                "--ema-normalize",  # Flicker reduction
                "--ema-decay", "0.75",  # Flicker resolution decay
                "--ema-buffer", "150",  # Flicker resolution buffer
                "--scene-detect",  # Scene boundary detection
                "--preserve-screen-border",  # Preserve screen border
                "--yes",  # Auto-confirm prompts
            ]

            # Add model config if provided (allows runtime override)
            if model_config:
                if "divergence" in model_config:
                    iw3_cmd[iw3_cmd.index("--divergence") + 1] = str(model_config["divergence"])
                if "convergence" in model_config:
                    iw3_cmd[iw3_cmd.index("--convergence") + 1] = str(model_config["convergence"])

            print(f"[Chunk {chunk_id}] Running: {' '.join(iw3_cmd)}")
            result = subprocess.run(
                iw3_cmd,
                capture_output=True,
                text=True,
                cwd="/root/nunif"
            )

            if result.returncode != 0:
                raise RuntimeError(f"iw3 conversion failed: {result.stderr}\n{result.stdout}")

            print(f"[Chunk {chunk_id}] 3D conversion complete (VDA_L)")

            # Get output file size for metadata
            output_size = output_path.stat().st_size

            print(f"[Chunk {chunk_id}] Uploading to S3: {s3_output_key}")
            s3_client.upload_file(
                str(output_path),
                s3_bucket,
                s3_output_key,
                ExtraArgs={'ContentType': 'video/mp4'}
            )

            print(f"[Chunk {chunk_id}] Conversion complete!")

            return {
                "status": "success",
                "chunk_id": chunk_id,
                "job_id": job_id,
                "s3_output_key": s3_output_key,
                "output_size_bytes": output_size,
                "duration_seconds": duration,
            }

    except Exception as e:
        error_msg = f"Error processing chunk {chunk_id}: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        return {
            "status": "error",
            "chunk_id": chunk_id,
            "job_id": job_id,
            "error": error_msg,
        }


@app.function(
    image=image,
    secrets=[s3_secret],
    timeout=1800,  # 30 minutes for combining
)
def combine_video_chunks(
    s3_bucket: str,
    chunk_keys: list[str],
    output_key: str,
    job_id: str,
) -> dict:
    """
    Download and combine video chunks into final output.

    Args:
        s3_bucket: S3 bucket name
        chunk_keys: List of S3 keys for chunks (in order)
        output_key: S3 key for final output
        job_id: Job identifier

    Returns:
        Dict with status and metadata
    """
    import boto3
    import subprocess
    import traceback

    s3_client = boto3.client('s3')

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            concat_file = tmpdir_path / "concat.txt"
            output_path = tmpdir_path / "final.mp4"

            # Download all chunks
            chunk_paths = []
            for i, chunk_key in enumerate(chunk_keys):
                chunk_path = tmpdir_path / f"chunk_{i}.mp4"
                print(f"Downloading chunk {i}: {chunk_key}")
                s3_client.download_file(s3_bucket, chunk_key, str(chunk_path))
                chunk_paths.append(chunk_path)

            # Create concat file for ffmpeg
            with open(concat_file, 'w') as f:
                for chunk_path in chunk_paths:
                    f.write(f"file '{chunk_path}'\n")

            print("Combining chunks with ffmpeg")
            combine_cmd = [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_file),
                "-c", "copy",
                str(output_path)
            ]
            subprocess.run(combine_cmd, check=True, capture_output=True)

            output_size = output_path.stat().st_size

            print(f"Uploading final video to S3: {output_key}")
            s3_client.upload_file(
                str(output_path),
                s3_bucket,
                output_key,
                ExtraArgs={'ContentType': 'video/mp4'}
            )

            print("Combining complete!")

            return {
                "status": "success",
                "job_id": job_id,
                "s3_output_key": output_key,
                "output_size_bytes": output_size,
                "num_chunks": len(chunk_keys),
            }

    except Exception as e:
        error_msg = f"Error combining chunks: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        return {
            "status": "error",
            "job_id": job_id,
            "error": error_msg,
        }
