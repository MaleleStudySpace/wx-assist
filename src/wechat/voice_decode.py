"""WeChat voice (SILK) decoder.

Converts SILK_V3 encoded voice data to WAV or MP3 format.
SILK_V3 is WeChat's voice codec (24kHz mono).

Usage:
    from src.wechat.voice_decode import silk_to_wav, silk_to_mp3

    # From hex string (DLL output)
    wav_data = silk_to_wav(hex_silk_data)

    # From raw bytes
    wav_data = silk_to_wav_bytes(raw_silk_data)
"""

import io
import logging
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# SILK_V3 magic header (after optional 0x02 prefix)
SILK_V3_MAGIC = b'#!SILK_V3'

# WAV header constants
SAMPLE_RATE = 24000  # WeChat SILK uses 24kHz
CHANNELS = 1  # Mono
SAMPLE_WIDTH = 2  # 16-bit


def silk_to_wav(hex_silk_data: str) -> Optional[bytes]:
    """Convert hex-encoded SILK data to WAV bytes.

    Args:
        hex_silk_data: Hex-encoded string from DLL wcdb_get_voice_data

    Returns:
        WAV file bytes, or None on failure
    """
    try:
        silk_bytes = bytes.fromhex(hex_silk_data)
        return silk_to_wav_bytes(silk_bytes)
    except Exception as e:
        logger.warning("silk_to_wav failed: %s", e)
        return None


def silk_to_wav_bytes(silk_bytes: bytes) -> Optional[bytes]:
    """Convert raw SILK bytes to WAV bytes.

    Args:
        silk_bytes: Raw SILK data (with or without 0x02 prefix)

    Returns:
        WAV file bytes, or None on failure
    """
    # Strip optional 0x02 prefix
    if silk_bytes[:1] == b'\x02':
        silk_data = silk_bytes[1:]
    else:
        silk_data = silk_bytes

    # Validate SILK_V3 header
    if not silk_data.startswith(SILK_V3_MAGIC):
        logger.warning("Invalid SILK data: missing SILK_V3 header")
        return None

    # Decode SILK to PCM - try pilk first, then pysilk, then ffmpeg
    decode_method = None

    try:
        import pilk
        decode_method = 'pilk'
    except ImportError:
        pass

    if decode_method == 'pilk':
        try:
            with tempfile.NamedTemporaryFile(mode='wb', suffix='.silk', delete=False) as silk_file:
                silk_file.write(silk_data)
                silk_path = silk_file.name

            pcm_path = silk_path.replace('.silk', '.pcm')
            pilk.decode(silk_path, pcm_path)

            with open(pcm_path, 'rb') as f:
                pcm_data = f.read()

            wav_data = _build_wav(pcm_data, SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH)

            Path(silk_path).unlink(missing_ok=True)
            Path(pcm_path).unlink(missing_ok=True)

            return wav_data

        except Exception as e:
            logger.warning("pilk decode failed, trying pysilk: %s", e)
            for p in [silk_path, pcm_path]:
                try:
                    Path(p).unlink(missing_ok=True)
                except:
                    pass

    # Try pysilk (silk-python package, installs as pysilk module)
    try:
        from pysilk import decode as pysilk_decode
        decode_method = 'pysilk'
    except ImportError:
        pass

    if decode_method == 'pysilk':
        try:
            # pysilk.decode(input_io, output_io, sample_rate) -> bytes
            input_buf = io.BytesIO(silk_data)
            output_buf = io.BytesIO()
            result = pysilk_decode(input_buf, output_buf, SAMPLE_RATE)
            pcm_data = output_buf.getvalue() or result
            if pcm_data:
                wav_data = _build_wav(pcm_data, SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH)
                return wav_data
            logger.warning("pysilk returned empty data")
        except Exception as e:
            logger.warning("pysilk decode failed, trying ffmpeg: %s", e)

    # Fallback: try ffmpeg (supports SILK codec natively)
    try:
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.silk', delete=False) as silk_file:
            silk_file.write(silk_data)
            silk_path = silk_file.name

        wav_path = silk_path.replace('.silk', '.wav')

        result = subprocess.run(
            ['ffmpeg', '-y', '-i', silk_path, '-ar', str(SAMPLE_RATE),
             '-ac', str(CHANNELS), '-sample_fmt', 's16', wav_path],
            capture_output=True, timeout=10
        )

        if result.returncode == 0:
            with open(wav_path, 'rb') as f:
                wav_data = f.read()
            Path(silk_path).unlink(missing_ok=True)
            Path(wav_path).unlink(missing_ok=True)
            return wav_data

        logger.error("ffmpeg SILK decode failed: %s", result.stderr.decode('utf-8', errors='replace')[:200])
        for p in [silk_path, wav_path]:
            try:
                Path(p).unlink(missing_ok=True)
            except:
                pass

    except FileNotFoundError:
        logger.error("Neither pilk, pysilk, nor ffmpeg available for SILK decoding")
    except Exception as e:
        logger.error("SILK decode fallback failed: %s", e)

    return None


def silk_to_mp3(hex_silk_data: str) -> Optional[bytes]:
    """Convert hex-encoded SILK data to MP3 bytes.

    Requires ffmpeg to be installed and in PATH.

    Args:
        hex_silk_data: Hex-encoded string from DLL wcdb_get_voice_data

    Returns:
        MP3 file bytes, or None on failure
    """
    try:
        silk_bytes = bytes.fromhex(hex_silk_data)
        return silk_to_mp3_bytes(silk_bytes)
    except Exception as e:
        logger.warning("silk_to_mp3 failed: %s", e)
        return None


def silk_to_mp3_bytes(silk_bytes: bytes) -> Optional[bytes]:
    """Convert raw SILK bytes to MP3 bytes.

    Requires ffmpeg to be installed and in PATH.

    Args:
        silk_bytes: Raw SILK data (with or without 0x02 prefix)

    Returns:
        MP3 file bytes, or None on failure
    """
    # Convert to WAV first
    wav_data = silk_to_wav_bytes(silk_bytes)
    if not wav_data:
        return None

    # Check if ffmpeg is available
    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5)
        if result.returncode != 0:
            logger.error("ffmpeg not found or not working")
            return None
    except Exception as e:
        logger.error("ffmpeg check failed: %s", e)
        return None

    try:
        # Write WAV to temp file
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.wav', delete=False) as wav_file:
            wav_file.write(wav_data)
            wav_path = wav_file.name

        mp3_path = wav_path.replace('.wav', '.mp3')

        # Convert WAV to MP3 using ffmpeg
        result = subprocess.run([
            'ffmpeg', '-y',
            '-i', wav_path,
            '-codec:a', 'libmp3lame',
            '-b:a', '128k',
            mp3_path
        ], capture_output=True, timeout=30)

        if result.returncode != 0:
            logger.error("ffmpeg conversion failed: %s", result.stderr.decode('utf-8', errors='replace'))
            return None

        # Read MP3 data
        with open(mp3_path, 'rb') as f:
            mp3_data = f.read()

        # Cleanup temp files
        Path(wav_path).unlink(missing_ok=True)
        Path(mp3_path).unlink(missing_ok=True)

        return mp3_data

    except Exception as e:
        logger.error("MP3 conversion failed: %s", e)
        # Cleanup temp files on error
        for p in [wav_path, mp3_path]:
            try:
                Path(p).unlink(missing_ok=True)
            except:
                pass
        return None


def _build_wav(pcm_data: bytes, sample_rate: int, channels: int, sample_width: int) -> bytes:
    """Build WAV file from PCM data.

    Args:
        pcm_data: Raw PCM samples (16-bit little-endian)
        sample_rate: Sample rate in Hz
        channels: Number of audio channels
        sample_width: Bytes per sample (2 for 16-bit)

    Returns:
        Complete WAV file bytes
    """
    # WAV header structure
    # RIFF header
    data_size = len(pcm_data)
    file_size = 36 + data_size

    header = bytearray()

    # RIFF chunk
    header.extend(b'RIFF')
    header.extend(struct.pack('<I', file_size))
    header.extend(b'WAVE')

    # fmt chunk
    header.extend(b'fmt ')
    header.extend(struct.pack('<I', 16))  # fmt chunk size
    header.extend(struct.pack('<H', 1))   # PCM format
    header.extend(struct.pack('<H', channels))
    header.extend(struct.pack('<I', sample_rate))
    byte_rate = sample_rate * channels * sample_width
    header.extend(struct.pack('<I', byte_rate))
    block_align = channels * sample_width
    header.extend(struct.pack('<H', block_align))
    header.extend(struct.pack('<H', sample_width * 8))  # bits per sample

    # data chunk
    header.extend(b'data')
    header.extend(struct.pack('<I', data_size))
    header.extend(pcm_data)

    return bytes(header)


def get_silk_duration(hex_silk_data: str) -> Optional[float]:
    """Estimate SILK voice duration from hex data.

    This is a rough estimate based on data size.

    Args:
        hex_silk_data: Hex-encoded SILK data

    Returns:
        Estimated duration in seconds, or None on failure
    """
    try:
        silk_bytes = bytes.fromhex(hex_silk_data)
        # Strip prefix
        if silk_bytes[:1] == b'\x02':
            silk_bytes = silk_bytes[1:]

        # Rough estimate: ~20 bytes per ms at 24kHz
        # This is very approximate; actual duration varies
        data_size = len(silk_bytes)
        estimated_duration_ms = data_size / 20.0
        return estimated_duration_ms / 1000.0
    except Exception:
        return None
