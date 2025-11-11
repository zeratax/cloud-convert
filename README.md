# Cloud Convert - 3D Video Conversion

Cloud-based 3D video conversion using [Modal](https://modal.com) GPU infrastructure and [iw3](https://github.com/nagadomi/nunif) (nunif) for immersive video conversion using the Video Depth Anything Large (VDA_L) model.

## Features

- **Parallel GPU Processing**: Automatically splits long videos across up to 8 B200 GPUs
- **Resumable Jobs**: Interruptions? No problem. Resume from where you left off
- **Smart Splitting**: Only splits videos longer than configurable threshold (default: 8 minutes)
- **Failure Isolation**: Each chunk processed independently - one failure doesn't kill the entire job
- **S3 State Management**: Job state stored in S3 for durability and cross-machine resumability
- **Progress Tracking**: Real-time progress bars for uploads, downloads, and conversion

## Architecture

```
┌─────────────┐
│ Local       │
│ Input Video │
└──────┬──────┘
       │ Upload to S3
       ▼
┌─────────────┐
│ S3 Storage  │  ◄──────┐
│ + Job State │         │ State tracking
└──────┬──────┘         │
       │                │
       │ Trigger        │
       ▼                │
┌─────────────────────┐ │
│ Modal Orchestrator  │─┘
│ - Analyze video     │
│ - Split if needed   │
│ - Spawn GPU workers │
└──────┬──────────────┘
       │
       │ Parallel processing
       ▼
┌─────────────┐  ┌─────────────┐       ┌─────────────┐
│ GPU Worker  │  │ GPU Worker  │  ...  │ GPU Worker  │
│ Chunk 0     │  │ Chunk 1     │       │ Chunk 7     │
│ (B200)      │  │ (B200)      │       │ (B200)      │
└──────┬──────┘  └──────┬──────┘       └──────┬──────┘
       │                │                     │
       │ Upload chunks  │                     │
       └────────────────┴─────────────────────┘
                        │
                        ▼
              ┌─────────────────┐
              │ Combine Chunks  │
              │ (FFmpeg concat) │
              └────────┬────────┘
                       │
                       │ Download final
                       ▼
              ┌─────────────────┐
              │ Local           │
              │ Output Video    │
              └─────────────────┘
```

## Prerequisites

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) package manager
- [Modal](https://modal.com) account
- AWS S3 bucket
- FFmpeg installed locally

## Installation

1. **Clone the repository:**

```bash
git clone <your-repo>
cd cloud-convert
```

2. **Install dependencies with uv:**

```bash
uv sync
```

3. **Install Modal CLI:**

```bash
uv pip install modal
modal token new
```

4. **Run setup wizard:**

```bash
uv run python cli.py setup
```

Or manually configure `.env`:

```bash
cp .env.example .env
# Edit .env with your credentials
```

5. **Create Modal secret for AWS:**

```bash
modal secret create aws-s3 \
  AWS_ACCESS_KEY_ID=your_key \
  AWS_SECRET_ACCESS_KEY=your_secret \
  AWS_REGION=us-east-1
```

## Usage

### Convert a Video

Basic conversion:

```bash
uv run python cli.py convert input.mp4
```

With custom output path:

```bash
uv run python cli.py convert input.mp4 -o output_3d.mp4
```

With custom settings:

```bash
uv run python cli.py convert input.mp4 \
  --max-gpus 4 \
  --split-threshold 300 \
  --model default
```

### Resume a Failed Job

If a job fails or is interrupted, resume it:

```bash
uv run python cli.py convert input.mp4 --resume <job-id>
```

### List All Jobs

```bash
# All jobs
uv run python cli.py list-jobs

# Filter by status
uv run python cli.py list-jobs --status converting
uv run python cli.py list-jobs --status failed
```

### Check Job Status

```bash
uv run python cli.py status <job-id>
```

### Download Completed Job

```bash
uv run python cli.py download <job-id> output.mp4
```

## Configuration

Edit `.env` to customize:

| Variable | Description | Default |
|----------|-------------|---------|
| `MAX_GPUS` | Maximum GPUs to use | 8 |
| `SPLIT_THRESHOLD_SECONDS` | Split videos longer than this | 480 (8 min) |
| `CHUNK_OVERLAP_SECONDS` | Overlap between chunks | 2 |
| `IW3_MODEL` | iw3 model configuration | default |

## How It Works

### Smart Splitting Logic

- Videos **< 8 minutes**: Processed as single chunk on 1 GPU
- Videos **≥ 8 minutes**: Split into chunks (~1 minute per GPU)
- Each chunk has 2-second overlap to prevent artifacts at boundaries

### Resumability

Job state is stored in S3 at `s3://your-bucket/states/<job-id>.state.json`:

```json
{
  "job_id": "uuid",
  "status": "converting",
  "chunks": [
    {
      "chunk_id": 0,
      "status": "completed",
      "s3_output_key": "outputs/uuid/chunk_000.mp4"
    },
    {
      "chunk_id": 1,
      "status": "failed",
      "attempt_count": 2,
      "error_message": "..."
    }
  ]
}
```

When resuming, only pending/failed chunks are reprocessed.

### Failure Isolation

Each chunk is processed independently:
- Chunk 0 succeeds ✓
- Chunk 1 fails ✗ → Only chunk 1 is retried
- Chunk 2 succeeds ✓

No wasted GPU time reprocessing successful chunks!

## iw3 / VDA_L Model

The system uses **iw3** (from [nagadomi/nunif](https://github.com/nagadomi/nunif)) with the **Video Depth Anything Large (VDA_L)** model for 3D conversion:

### Features:
- **Model**: VDA_L (Video Depth Anything Large) - temporal consistency for smooth video
- **Output Format**: Full Side-by-Side (SBS) for VR headset compatibility
- **Depth Estimation**: AI-powered monocular depth estimation
- **Quality Settings**:
  - Divergence: 2.0 (3D strength)
  - Convergence: 0.5 (edge viewing comfort)

### Customization:

You can adjust 3D parameters by passing model_config to the converter:

```python
from converter import VideoConverter

converter = VideoConverter(s3_bucket="your-bucket")
converter.convert_video(
    "video.mp4",
    model_config={
        "divergence": 3.0,    # Stronger 3D effect (1.0-4.0)
        "convergence": 0.7,   # Better for curved displays (0.0-1.0)
    }
)
```

Or via CLI (future enhancement - currently uses defaults).

## Cost Optimization

Modal charges by GPU-second. Tips to minimize costs:

1. **Batch processing**: Queue multiple videos and process together
2. **Adjust split threshold**: Lower threshold = more parallelization but more overhead
3. **Monitor failures**: Use `list-jobs --status failed` to catch issues early
4. **Use appropriate models**: Faster iw3 models = lower costs

## Troubleshooting

### Job stuck in "converting" status

```bash
# Check detailed status
uv run python cli.py status <job-id>

# Resume to retry failed chunks
uv run python cli.py convert input.mp4 --resume <job-id>
```

### "ModuleNotFoundError: No module named 'modal'"

```bash
uv sync
```

### Modal authentication errors

```bash
modal token new
```

### FFmpeg not found

```bash
# Ubuntu/Debian
sudo apt install ffmpeg

# macOS
brew install ffmpeg
```

## Development

Run in development mode:

```bash
# Install dev dependencies
uv sync --dev

# Run tests (when implemented)
uv run pytest

# Format code
uv run black .
uv run ruff check .
```

## Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests and formatters
5. Submit a pull request

## License

MIT License - see LICENSE file for details

## Acknowledgments

- [Modal](https://modal.com) for GPU infrastructure
- [nagadomi/nunif](https://github.com/nagadomi/nunif) for iw3 and VDA_L 3D conversion
- [FFmpeg](https://ffmpeg.org) for video processing
