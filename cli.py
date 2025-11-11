#!/usr/bin/env python3
"""
CLI for cloud-based 3D video conversion.
"""
import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

from converter import VideoConverter
from state_manager import JobStatus

# Load environment variables
load_dotenv()


@click.group()
@click.version_option(version='0.1.0')
def cli():
    """Cloud-based 3D video converter using Modal and iw3."""
    pass


@cli.command()
@click.argument('input_video', type=click.Path(exists=True))
@click.option('--output', '-o', type=click.Path(), help='Output video path')
@click.option('--resume', '-r', help='Resume job ID')
@click.option('--max-gpus', type=int, default=None, help='Maximum number of GPUs to use')
@click.option('--split-threshold', type=float, default=None, help='Split threshold in seconds')
@click.option('--model', default='default', help='iw3 model to use')
def convert(input_video, output, resume, max_gpus, split_threshold, model):
    """Convert a video to 3D format."""

    # Check required environment variables
    s3_bucket = os.getenv('S3_BUCKET')
    if not s3_bucket:
        click.echo("Error: S3_BUCKET not set in environment", err=True)
        click.echo("Copy .env.example to .env and configure it", err=True)
        sys.exit(1)

    # Get configuration from environment or use defaults
    if max_gpus is None:
        max_gpus = int(os.getenv('MAX_GPUS', '8'))

    if split_threshold is None:
        split_threshold = float(os.getenv('SPLIT_THRESHOLD_SECONDS', '480'))

    # Create converter
    converter = VideoConverter(
        s3_bucket=s3_bucket,
        max_gpus=max_gpus,
        split_threshold_seconds=split_threshold,
    )

    # Model configuration
    model_config = {'model': model}

    try:
        job_id = converter.convert_video(
            input_video=input_video,
            output_video=output,
            job_id=resume,
            model_config=model_config,
        )

        click.echo(f"\n✓ Success!")
        click.echo(f"  Job ID: {job_id}")

        if output:
            click.echo(f"  Output: {output}")

    except Exception as e:
        click.echo(f"\n✗ Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option('--status', type=click.Choice(['initialized', 'splitting', 'converting', 'combining', 'completed', 'failed']), help='Filter by status')
def list_jobs(status):
    """List all conversion jobs."""

    s3_bucket = os.getenv('S3_BUCKET')
    if not s3_bucket:
        click.echo("Error: S3_BUCKET not set in environment", err=True)
        sys.exit(1)

    converter = VideoConverter(s3_bucket=s3_bucket)

    status_filter = JobStatus(status) if status else None
    converter.list_jobs(status_filter)


@cli.command()
@click.argument('job_id')
def status(job_id):
    """Show detailed status of a job."""

    s3_bucket = os.getenv('S3_BUCKET')
    if not s3_bucket:
        click.echo("Error: S3_BUCKET not set in environment", err=True)
        sys.exit(1)

    from state_manager import StateManager

    state_manager = StateManager(s3_bucket)
    job_state = state_manager.load_state(job_id)

    if not job_state:
        click.echo(f"Job not found: {job_id}", err=True)
        sys.exit(1)

    # Print job details
    click.echo(f"\nJob ID: {job_state.job_id}")
    click.echo(f"Status: {job_state.status.value}")
    click.echo(f"Video: {job_state.video_name}")
    click.echo(f"Duration: {job_state.total_duration:.2f}s")
    click.echo(f"Chunks: {job_state.num_chunks}")
    click.echo(f"Created: {job_state.created_at}")
    click.echo(f"Updated: {job_state.updated_at}")

    if job_state.s3_output_key:
        click.echo(f"Output: s3://{s3_bucket}/{job_state.s3_output_key}")

    # Print chunk details
    click.echo(f"\nChunks:")
    click.echo(f"  {'ID':<4} {'Status':<12} {'Time Range':<20} {'Attempts':<10}")
    click.echo("  " + "-" * 50)

    for chunk in job_state.chunks:
        time_range = f"{chunk.start_time:.1f}s - {chunk.end_time:.1f}s"
        click.echo(f"  {chunk.chunk_id:<4} {chunk.status.value:<12} {time_range:<20} {chunk.attempt_count:<10}")

        if chunk.error_message:
            click.echo(f"       Error: {chunk.error_message[:60]}...")


@cli.command()
@click.argument('job_id')
@click.argument('output_path', type=click.Path())
def download(job_id, output_path):
    """Download the result of a completed job."""

    s3_bucket = os.getenv('S3_BUCKET')
    if not s3_bucket:
        click.echo("Error: S3_BUCKET not set in environment", err=True)
        sys.exit(1)

    from state_manager import StateManager

    state_manager = StateManager(s3_bucket)
    job_state = state_manager.load_state(job_id)

    if not job_state:
        click.echo(f"Job not found: {job_id}", err=True)
        sys.exit(1)

    if job_state.status != JobStatus.COMPLETED:
        click.echo(f"Job is not completed (status: {job_state.status.value})", err=True)
        sys.exit(1)

    if not job_state.s3_output_key:
        click.echo("No output file available", err=True)
        sys.exit(1)

    converter = VideoConverter(s3_bucket=s3_bucket)

    click.echo(f"Downloading: {job_state.s3_output_key}")
    converter.download_from_s3(job_state.s3_output_key, output_path)

    click.echo(f"✓ Downloaded to: {output_path}")


@cli.command()
def setup():
    """Setup wizard for first-time configuration."""

    env_file = Path('.env')

    if env_file.exists():
        click.echo("Found existing .env file")
        if not click.confirm("Overwrite?"):
            return

    click.echo("\n=== Cloud Convert Setup ===\n")

    # AWS Configuration
    click.echo("AWS S3 Configuration:")
    aws_key = click.prompt("AWS Access Key ID")
    aws_secret = click.prompt("AWS Secret Access Key", hide_input=True)
    aws_region = click.prompt("AWS Region", default="us-east-1")
    s3_bucket = click.prompt("S3 Bucket Name")

    # Modal Configuration
    click.echo("\nModal Configuration:")
    click.echo("Run 'modal token new' to get your token if you haven't already")
    modal_token_id = click.prompt("Modal Token ID")
    modal_token_secret = click.prompt("Modal Token Secret", hide_input=True)

    # Processing Configuration
    click.echo("\nProcessing Configuration:")
    max_gpus = click.prompt("Maximum GPUs", default=8, type=int)
    split_threshold = click.prompt("Split threshold (seconds)", default=480, type=int)

    # Write .env file
    with open(env_file, 'w') as f:
        f.write(f"# AWS S3 Configuration\n")
        f.write(f"AWS_ACCESS_KEY_ID={aws_key}\n")
        f.write(f"AWS_SECRET_ACCESS_KEY={aws_secret}\n")
        f.write(f"AWS_REGION={aws_region}\n")
        f.write(f"S3_BUCKET={s3_bucket}\n\n")

        f.write(f"# Modal Configuration\n")
        f.write(f"MODAL_TOKEN_ID={modal_token_id}\n")
        f.write(f"MODAL_TOKEN_SECRET={modal_token_secret}\n\n")

        f.write(f"# Processing Configuration\n")
        f.write(f"MAX_GPUS={max_gpus}\n")
        f.write(f"SPLIT_THRESHOLD_SECONDS={split_threshold}\n")
        f.write(f"CHUNK_OVERLAP_SECONDS=2\n\n")

        f.write(f"# iw3 Configuration\n")
        f.write(f"IW3_MODEL=default\n")

    click.echo(f"\n✓ Configuration saved to .env")

    # Setup Modal secret
    click.echo("\nSetting up Modal secret...")
    click.echo("Run this command to create the AWS secret in Modal:")
    click.echo(f"\n  modal secret create aws-s3 \\")
    click.echo(f"    AWS_ACCESS_KEY_ID={aws_key} \\")
    click.echo(f"    AWS_SECRET_ACCESS_KEY=*** \\")
    click.echo(f"    AWS_REGION={aws_region}\n")


if __name__ == '__main__':
    cli()
