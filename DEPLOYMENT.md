# Deployment Guide

This guide will help you deploy and test the cloud video conversion system.

## System Overview

**Production-Ready:**
- ✅ iw3 with VDA_L model for 3D conversion
- ✅ Full Side-by-Side (SBS) output for VR compatibility
- ✅ Parallel GPU processing on Modal
- ✅ S3 state management for resumability
- ✅ Smart video chunking with overlap

**Requires Setup:**
- ⚠️ Modal app deployment
- ⚠️ AWS credentials as Modal secret
- ⚠️ Local .env configuration

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

Download a test video and convert it:

```bash
# Download Big Buck Bunny (10 minute test video)
wget -O big_buck_bunny.mp4 "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4"

# Convert to 3D (will split into 8 chunks)
uv run python cli.py convert big_buck_bunny.mp4 -o big_buck_bunny_3d.mp4

# Or test with a shorter video first:
wget -O test_short.mp4 "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4"
uv run python cli.py convert test_short.mp4 -o test_short_3d.mp4

# Check job status
uv run python cli.py list-jobs

# Resume if needed
uv run python cli.py convert video.mp4 --resume <job-id>
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

## iw3 / VDA_L Configuration

The system is **already configured** with iw3 and the Video Depth Anything Large (VDA_L) model:

### Current Settings:
- **Model**: VDA_L (Video Depth Anything Large)
- **Output Format**: Full Side-by-Side (SBS) for VR headsets
- **Divergence**: 2.0 (3D strength)
- **Convergence**: 0.5 (edge viewing comfort)

### Customization:

To adjust 3D parameters, modify `modal_app.py` line 124-127:

```python
"--divergence", "3.0",     # Stronger 3D (1.0-4.0)
"--convergence", "0.7",    # Better for curved displays (0.0-1.0)
```

Or use different depth models:
- `VDA_L` - Video Depth Anything Large (default, best quality)
- `VDA_Metric_L` - With metric depth
- `DA_V2_L` - Depth Anything V2 Large
- `ZoeDepth` - Alternative depth model

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

## S3 Structure

After processing videos, your S3 bucket will contain:

```
s3://your-bucket/
├── inputs/<job-id>/
│   └── video.mp4                    # Original uploaded video
├── states/<job-id>.state.json       # Job state for resumability
└── outputs/<job-id>/
    ├── chunk_000.mp4                # Processed chunk 0
    ├── chunk_001.mp4                # Processed chunk 1
    ├── ...
    └── final.mp4                    # Combined 3D result
```

**Note**: The final 3D video will have `_LRF_Full_SBS` in the filename for VR player compatibility.
