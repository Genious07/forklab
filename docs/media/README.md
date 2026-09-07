# Walkthrough media

Real browser screenshots from the synthetic demo, engine forklab-sim-0.1.1.
No real customer data is included. The GIF and silent MP4 are screenshot tours,
not screen recordings and not timing benchmarks. The written walkthrough provides
an alternative to animation and the PNGs retain their original screenshot resolution.

Tour chapters: workbench (0s), policy (5s), process (10s), comparison (15s), order
trace (22s). Total duration: 29 seconds. GIF loops; MP4 supports pausing and seeking
in a compatible player. GitHub may offer download rather than inline playback for
repository-relative MP4 links, so the README uses the GIF as its inline preview.

Rebuild from these PNGs using FFmpeg:

```bash
ffmpeg -y -safe 0 -f concat -i docs/media/walkthrough.ffconcat \
  -vf 'fps=10,scale=860:-2,format=yuv420p' -t 29 \
  -c:v libx264 -crf 23 -movflags +faststart docs/media/forklab-walkthrough.mp4

ffmpeg -y -i docs/media/forklab-walkthrough.mp4 \
  -filter_complex 'fps=2,scale=720:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer' \
  -loop 0 docs/media/forklab-walkthrough.gif
```

Assets are covered by the repository MIT license.
