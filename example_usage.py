#!/usr/bin/env python3
"""
Example usage of the cloud video converter programmatically.
"""
import os
from dotenv import load_dotenv
from converter import VideoConverter

# Load environment
load_dotenv()


def main():
    """Example conversion workflow."""

    # Initialize converter
    converter = VideoConverter(
        s3_bucket=os.getenv('S3_BUCKET'),
        max_gpus=8,
        split_threshold_seconds=480,  # 8 minutes
    )

    # Convert a video
    input_video = "my_video.mp4"
    output_video = "my_video_3d.mp4"

    print(f"Converting {input_video} to 3D...")

    try:
        job_id = converter.convert_video(
            input_video=input_video,
            output_video=output_video,
            model_config={'model': 'default'}
        )

        print(f"Success! Job ID: {job_id}")
        print(f"Output saved to: {output_video}")

    except Exception as e:
        print(f"Error: {e}")

        # You can resume the job later with:
        # job_id = converter.convert_video(
        #     input_video=input_video,
        #     output_video=output_video,
        #     job_id=job_id  # Resume from this ID
        # )


def resume_example():
    """Example of resuming a failed job."""

    converter = VideoConverter(s3_bucket=os.getenv('S3_BUCKET'))

    # Resume a specific job
    job_id = "your-job-id-here"

    print(f"Resuming job {job_id}...")

    job_id = converter.convert_video(
        input_video="original_input.mp4",
        output_video="output_3d.mp4",
        job_id=job_id  # This resumes the job
    )

    print(f"Job completed: {job_id}")


def list_jobs_example():
    """Example of listing jobs."""

    converter = VideoConverter(s3_bucket=os.getenv('S3_BUCKET'))

    print("All jobs:")
    converter.list_jobs()

    print("\nFailed jobs only:")
    from state_manager import JobStatus
    converter.list_jobs(status_filter=JobStatus.FAILED)


if __name__ == '__main__':
    main()
