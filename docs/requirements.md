# RAMS Dependencies

These dependencies cover the full RAMS feature set (recording/detection, OAuth, music metadata editing, audits, and integrations). The `requirements.txt` file is intentionally left untouched—install these manually when setting up an environment.

## Python packages
- alembic==1.13.3
- APScheduler==3.10.4
- blinker==1.8.2
- cachelib==0.13.0
- click==8.1.7
- colorama==0.4.6
- ffmpeg-python==0.2.0
- Flask==3.0.3
- Flask-Migrate==4.0.7
- Flask-Session==0.8.0
- Flask-SQLAlchemy==3.1.1
- future==1.0.0
- greenlet==3.1.1
- itsdangerous==2.2.0
- Jinja2==3.1.4
- Mako==1.3.6
- MarkupSafe==3.0.2
- mod_wsgi==5.0.1
- msgspec==0.18.6
- numpy==2.1.3
- pydub==0.25.1
- python-dateutil==2.9.0.post0
- pytz==2024.2
- requests==2.32.3
- six==1.16.0
- SQLAlchemy==2.0.36
- typing_extensions==4.12.2
- tzdata==2024.2
- tzlocal==5.2
- Werkzeug==3.1.0
- Authlib (OAuth for Google/Discord; pin as needed)
- mutagen (audio metadata read/write for M4A/MP3/WAV/etc.)

## System requirements
- **FFmpeg** binary available on PATH (stream probing, waveform sampling, recording helpers).
- NAS-style paths for recordings, music library, and news/PSA imports.

## Install example
```bash
pip install -r requirements.txt
pip install Authlib mutagen
```

If you do not want to use `requirements.txt`, install the packages listed above directly with `pip`.
