# Deployment Guide

This guide will help you deploy and test the cloud video conversion system.

## System Status

✅ **Completed:**
- Dependencies installed with uv
- AWS S3 credentials configured
- Test video (Big Buck Bunny, 150 MB, 9.9 minutes) uploaded to S3
- Job state management tested
- Video analysis and chunking logic verified

⚠️ **Requires Modal Deployment:**
- Modal app needs to be deployed
- AWS credentials need to be added as Modal secret
- Actual GPU processing will happen after deployment

## Test Video Details

**Already in S3:**
- Location: `s3://dmnd-cloud-convert/inputs/36caa6c5-864a-4803-acc6-226ed9442aca/big_buck_bunny.mp4`
- Size: 150.7 MB
- Duration: 596 seconds (9.9 minutes)
- Will be split into: **8 chunks** (~75 seconds each)

This is perfect for testing the parallel processing!

## Step-by-Step Deployment

### 1. Fix S3 Permissions (Optional but Recommended)

The IAM user `cloud-convert` needs these permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:ListBucket",
        "s3:DeleteObject"
      ],
      "Resource": [
        "arn:aws:s3:::dmnd-cloud-convert",
        "arn:aws:s3:::dmnd-cloud-convert/*"
      ]
    }
  ]
}
```

Currently has: `s3:PutObject` ✅
Missing: `s3:GetObject` (needed for downloading results)

### 2. Set Up Modal Token

```bash
# Set Modal credentials
modal token set \
  --token-id <your-modal-token-id> \
  --token-secret <your-modal-token-secret>
```

### 3. Create Modal AWS Secret

This allows Modal workers to access S3:

```bash
modal secret create aws-s3 \
  AWS_ACCESS_KEY_ID=<your-aws-access-key> \
  AWS_SECRET_ACCESS_KEY=<your-aws-secret-key> \
  AWS_REGION=eu-central-1
```

Note: Use the same AWS credentials from your `.env` file.

### 4. Deploy Modal App

```bash
modal deploy modal_app.py
```

This will:
- Build a Docker image with FFmpeg and dependencies
- Deploy two Modal functions:
  - `convert_video_chunk`: Processes individual chunks on B200 GPUs
  - `combine_video_chunks`: Combines processed chunks into final video

### 5. Run Test Conversion

#### Option A: Use the CLI

```bash
# Full conversion
uv run python cli.py convert big_buck_bunny.mp4 -o big_buck_bunny_3d.mp4

# Check status
uv run python cli.py list-jobs

# Resume if needed
uv run python cli.py convert big_buck_bunny.mp4 --resume <job-id>
```

#### Option B: Use Existing Upload

Since Big Buck Bunny is already in S3, you can test with a smaller video first:

```bash
# Download a short test video (1 minute)
wget -O test_short.mp4 "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4"

# Convert it (will use single GPU, no splitting)
uv run python cli.py convert test_short.mp4 -o test_short_3d.mp4
```

## What to Expect

### For Big Buck Bunny (9.9 minutes):

```
1. Analysis: 596 seconds detected, will split into 8 chunks
2. Upload: Already done! (skipped)
3. State Creation: Job state created in S3
4. Processing: 8 chunks processed in parallel on Modal
   - Each chunk: ~75 seconds
   - Processing time: ~2-5 minutes per chunk (depends on GPU availability)
   - Total time: ~2-5 minutes (parallel) vs ~16-40 minutes (sequential)
5. Combining: FFmpeg concatenates chunks
6. Download: Final video downloaded locally
```

### Chunk Breakdown:

| Chunk | Start | End | Duration |
|-------|-------|-----|----------|
| 0 | 0s | 76.6s | 76.6s |
| 1 | 72.6s | 151.1s | 78.6s |
| 2 | 147.1s | 225.7s | 78.6s |
| 3 | 221.7s | 300.2s | 78.6s |
| 4 | 296.2s | 374.8s | 78.6s |
| 5 | 370.8s | 449.4s | 78.6s |
| 6 | 445.4s | 523.9s | 78.6s |
| 7 | 519.9s | 596.5s | 76.6s |

Note the 4-second overlap between chunks for smooth transitions!

## Monitoring Progress

### Check Job Status

```bash
uv run python cli.py status <job-id>
```

Output:
```
Job ID: xxx
Status: converting
Video: big_buck_bunny.mp4
Duration: 596.47s
Chunks: 8

Chunks:
  ID   Status       Time Range          Attempts
  0    completed    0.0s - 76.6s       1
  1    completed    72.6s - 151.1s     1
  2    processing   147.1s - 225.7s    1
  3    pending      221.7s - 300.2s    0
  ...
```

### List All Jobs

```bash
uv run python cli.py list-jobs
```

### Check Modal Logs

```bash
modal app logs video-3d-converter
```

## Configuring iw3

The current system uses FFmpeg re-encoding as a placeholder. To use actual 3D conversion:

### Edit `modal_app.py` line 111-131:

Replace the placeholder with your iw3 command:

```python
# Option 1: iw3 CLI
iw3_cmd = [
    "iw3", "convert",
    str(chunk_path),
    str(output_path),
    "--model", "depth-anything",
    "--format", "side-by-side",
]
subprocess.run(iw3_cmd, check=True)

# Option 2: iw3 Python API
from iw3 import convert_to_3d
convert_to_3d(
    str(chunk_path),
    str(output_path),
    model="depth-anything",
    format="side-by-side"
)
```

### Update iw3 installation (line 30-36):

```python
.run_commands(
    "pip install git+https://github.com/your-iw3-repo/iw3.git",
    # Or install from PyPI:
    # "pip install iw3",
)
```

Then redeploy:
```bash
modal deploy modal_app.py
```

## Cost Estimation

### Modal Pricing (B200 GPU):
- ~$3-5/hour per GPU
- 8 GPUs in parallel
- Processing time: ~2-5 minutes
- **Estimated cost per video: $1-3** (depending on actual GPU time)

### S3 Costs:
- Storage: ~$0.023/GB/month
- Upload: Free
- Download: ~$0.09/GB
- **Estimated per video: $0.01-0.05**

### Total per conversion: ~$1-3

## Troubleshooting

### "Modal connection failed"

Run: `modal token set --token-id ... --token-secret ...`

### "AWS credentials not found"

Check `.env` file exists and has correct credentials

### "Chunk failed to process"

Check Modal logs: `modal app logs video-3d-converter`

Resume the job: `uv run python cli.py convert video.mp4 --resume <job-id>`

### "S3 Access Denied"

Add `s3:GetObject` permission to IAM user policy

## Next Steps

1. Deploy to Modal (steps 2-4 above)
2. Test with a short video first
3. Then process Big Buck Bunny
4. Configure actual iw3 conversion
5. Process your own videos!

## S3 Resources

Current uploads:
- `s3://dmnd-cloud-convert/inputs/36caa6c5-864a-4803-acc6-226ed9442aca/big_buck_bunny.mp4`

After processing, you'll see:
- `s3://dmnd-cloud-convert/states/<job-id>.state.json` (job state)
- `s3://dmnd-cloud-convert/outputs/<job-id>/chunk_000.mp4` (processed chunks)
- `s3://dmnd-cloud-convert/outputs/<job-id>/chunk_001.mp4`
- ...
- `s3://dmnd-cloud-convert/outputs/<job-id>/final.mp4` (combined result)
