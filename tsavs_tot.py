#!/usr/bin/env python3
"""
TOT Info - AVSファイルのTrim範囲または動画全編のTOT（Time Offset Table）時刻を表示

使用方法:
python3 tsavs_tot.py -i input.ts [-a trim.avs] [-o output.json]
"""

from collections import namedtuple
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timedelta
import argparse
import re
import sys
import json


# ==============================================================================
# Constants
# ==============================================================================

TS_PACKET_SIZE = 188
SYNC_BYTE = 0x47
TOT_PID = 0x14
TOT_TABLE_ID = 0x73
PAT_PID = 0x00
PAT_TABLE_ID = 0x00

CLOCK_FREQ = 90000  # 90 kHz for PTS/DTS
VIDEO_PID_MIN = 0x100
VIDEO_PID_MAX = 0x1FF

VideoFrame = namedtuple('VideoFrame', ['pts'])


# ==============================================================================
# TSPacketUtil
# ==============================================================================

class TSPacketUtil:
    """MPEG-TS packet utility functions"""

    @staticmethod
    def get_pid(packet: bytes) -> int:
        """Extract PID from TS packet"""
        return ((packet[1] & 0x1F) << 8) | packet[2]

    @staticmethod
    def has_payload_start(packet: bytes) -> bool:
        """Check if packet has payload unit start indicator (PUSI)"""
        return (packet[1] & 0x40) != 0

    @staticmethod
    def has_adaptation_field(packet: bytes) -> bool:
        """Check if packet has adaptation field"""
        return (packet[3] & 0x20) != 0

    @staticmethod
    def get_pes_offset(packet: bytes) -> int:
        """Get PES payload offset in packet"""
        head_len = 4
        if TSPacketUtil.has_adaptation_field(packet):
            len_af = packet[4]
            head_len += 1 + len_af
        return head_len

    @staticmethod
    def parse_pts_dts(packet: bytes):
        """Parse PTS/DTS from PES header"""
        if not TSPacketUtil.has_payload_start(packet):
            return None, None

        offset = TSPacketUtil.get_pes_offset(packet)

        if offset + 9 > len(packet):
            return None, None

        if packet[offset] != 0x00 or packet[offset+1] != 0x00 or packet[offset+2] != 0x01:
            return None, None

        flags_2 = packet[offset + 7]
        pts_dts_flag = (flags_2 >> 6) & 0x03

        pes_header_data_length = packet[offset + 8]
        current_pos = offset + 9

        if pts_dts_flag == 0x00:
            return None, None

        if current_pos + pes_header_data_length > len(packet):
            return None, None

        def extract_pts_dts(p: bytes, pos: int) -> Optional[int]:
            if pos + 5 > len(p):
                return None
            return ((p[pos] & 0x0E) << 29) | \
                   ((p[pos+1] & 0xFF) << 22) | \
                   ((p[pos+2] & 0xFE) << 14) | \
                   ((p[pos+3] & 0xFF) << 7) | \
                   ((p[pos+4] & 0xFE) >> 1)

        pts = None
        dts = None

        if pts_dts_flag & 0x02:
            pts = extract_pts_dts(packet, current_pos)
            if pts is None:
                return None, None
            current_pos += 5

        if pts_dts_flag & 0x01:
            dts = extract_pts_dts(packet, current_pos)
            if dts is None and (pts_dts_flag == 0x03):
                return None, None

        return pts, dts

    @staticmethod
    def parse_pts(packet: bytes) -> Optional[int]:
        """Parse PTS from PES header"""
        pts, _ = TSPacketUtil.parse_pts_dts(packet)
        return pts


# ==============================================================================
# TOTParser
# ==============================================================================

class TOTParser:
    """TOT (Time Offset Table) parser"""

    @staticmethod
    def parse_tot(packet: bytes) -> Optional[datetime]:
        """
        Extract timestamp from TOT packet
        """
        if TSPacketUtil.get_pid(packet) != TOT_PID:
            return None

        if not TSPacketUtil.has_payload_start(packet):
            return None

        offset = TSPacketUtil.get_pes_offset(packet)
        if offset >= len(packet):
            return None

        pointer: int = packet[offset]
        offset += 1 + pointer

        if offset + 8 > len(packet):
            return None

        if packet[offset] != TOT_TABLE_ID:
            return None

        mjd: int = (packet[offset + 3] << 8) | packet[offset + 4]
        hour_bcd: int = packet[offset + 5]
        min_bcd: int = packet[offset + 6]
        sec_bcd: int = packet[offset + 7]

        hour: int = ((hour_bcd >> 4) * 10) + (hour_bcd & 0x0F)
        minute: int = ((min_bcd >> 4) * 10) + (min_bcd & 0x0F)
        second: int = ((sec_bcd >> 4) * 10) + (sec_bcd & 0x0F)

        y_prime: int = int((mjd - 15078.2) / 365.25)
        m_prime: int = int((mjd - 14956.1 - int(y_prime * 365.25)) / 30.6001)
        day: int = mjd - 14956 - int(y_prime * 365.25) - int(m_prime * 30.6001)
        k: int = 1 if m_prime in (14, 15) else 0

        year: int = y_prime + k + 1900
        month: int = m_prime - 1 - k * 12

        try:
            return datetime(year, month, day, hour, minute, second)
        except ValueError:
            return None


# ==============================================================================
# PAT Parser
# ==============================================================================

def parse_pat_section(section_data: bytes) -> List[int]:
    if len(section_data) < 8:
        return []
    service_ids: List[int] = []
    section_offset: int = 8
    while section_offset + 4 <= len(section_data) - 4:
        program_number: int = (section_data[section_offset] << 8) | section_data[section_offset + 1]
        if program_number != 0:
            service_ids.append(program_number)
        section_offset += 4
    return service_ids


# ==============================================================================
# Section Collector
# ==============================================================================

class SectionCollector:
    def __init__(self):
        self.buffer: bytearray = bytearray()
        self.collecting: bool = False
        self.section_length: int = 0

    def add_packet(self, packet: bytes) -> Optional[bytes]:
        if TSPacketUtil.has_payload_start(packet):
            self.buffer.clear()
            self.collecting = True
            payload_offset: int = TSPacketUtil.get_pes_offset(packet)
            if payload_offset >= len(packet): return None
            pointer: int = packet[payload_offset]
            payload_offset += 1 + pointer
            if payload_offset >= len(packet): return None
            if payload_offset + 3 <= len(packet):
                self.section_length = (((packet[payload_offset + 1] & 0x0F) << 8) | packet[payload_offset + 2]) + 3
            self.buffer.extend(packet[payload_offset:])
        elif self.collecting:
            payload_offset: int = TSPacketUtil.get_pes_offset(packet)
            if payload_offset < len(packet):
                self.buffer.extend(packet[payload_offset:])
        if self.collecting and self.section_length > 0 and len(self.buffer) >= self.section_length:
            section: bytes = bytes(self.buffer[:self.section_length])
            self.collecting = False
            return section
        return None


# ==============================================================================
# StreamAnalyzer
# ==============================================================================

class StreamAnalyzer:
    def __init__(self):
        self.service_id: Optional[int] = None

    def build_video_index(self, input_file: str, video_pid: int) -> List[VideoFrame]:
        video_index: List[VideoFrame] = []
        chunk_size: int = TS_PACKET_SIZE * 10000
        pat_collector = SectionCollector()
        with open(input_file, 'rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk: break
                n_packets: int = len(chunk) // TS_PACKET_SIZE
                for i in range(n_packets):
                    packet_offset: int = i * TS_PACKET_SIZE
                    packet_bytes = chunk[packet_offset:packet_offset+TS_PACKET_SIZE]
                    if packet_bytes[0] != SYNC_BYTE: continue
                    pid: int = TSPacketUtil.get_pid(packet_bytes)
                    if (self.service_id is None) and (pid == PAT_PID):
                        section = pat_collector.add_packet(packet_bytes)
                        if section and section[0] == PAT_TABLE_ID:
                            service_ids = parse_pat_section(section)
                            if service_ids: self.service_id = service_ids[0]
                    if pid == video_pid:
                        if TSPacketUtil.has_payload_start(packet_bytes):
                            pts, _ = TSPacketUtil.parse_pts_dts(packet_bytes)
                            if pts is not None: video_index.append(VideoFrame(pts))
        return video_index

    def find_video_pid(self, input_file: str) -> int:
        chunk_size: int = TS_PACKET_SIZE * 5000
        with open(input_file, 'rb') as f:
            data = f.read(chunk_size)
            for i in range(0, len(data), TS_PACKET_SIZE):
                pkt = data[i:i+TS_PACKET_SIZE]
                if len(pkt) < TS_PACKET_SIZE: continue
                pid: int = TSPacketUtil.get_pid(pkt)
                if VIDEO_PID_MIN <= pid <= VIDEO_PID_MAX:
                    if TSPacketUtil.has_payload_start(pkt):
                        if TSPacketUtil.parse_pts(pkt) is not None: return pid
        return VIDEO_PID_MIN


# ==============================================================================
# Helper functions
# ==============================================================================

def parse_avs_file(avs_file: str) -> List[tuple]:
    with open(avs_file, 'r', encoding='utf-8') as f:
        content = f.read()
    trim_pattern = re.compile(r'Trim\((\d+),(\d+)\)')
    matches = trim_pattern.findall(content)
    return [(int(start), int(end)) for start, end in matches]

def find_tot_near_frame(input_file: str, target_pts: int, video_pid: int, search_range: int = 50000) -> Optional[datetime]:
    chunk_size: int = TS_PACKET_SIZE * 10000
    found_target = False
    packets_since_target = 0
    last_video_pts = None
    tot_time = None
    tot_video_pts = None
    with open(input_file, 'rb') as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk: break
            n_packets = len(chunk) // TS_PACKET_SIZE
            for i in range(n_packets):
                packet = chunk[i*TS_PACKET_SIZE : (i+1)*TS_PACKET_SIZE]
                if packet[0] != SYNC_BYTE: continue
                pid = TSPacketUtil.get_pid(packet)
                if pid == video_pid and TSPacketUtil.has_payload_start(packet):
                    pts = TSPacketUtil.parse_pts(packet)
                    if pts is not None:
                        last_video_pts = pts
                        if pts >= target_pts and not found_target: found_target = True
                if pid == TOT_PID:
                    parsed_tot = TOTParser.parse_tot(packet)
                    if parsed_tot:
                        tot_time, tot_video_pts = parsed_tot, last_video_pts
                if found_target:
                    packets_since_target += 1
                    if tot_time and tot_video_pts:
                        diff = (target_pts - tot_video_pts) / CLOCK_FREQ
                        return tot_time + timedelta(seconds=diff)
                    if packets_since_target > search_range: return tot_time
    if tot_time and tot_video_pts:
        diff = (target_pts - tot_video_pts) / CLOCK_FREQ
        return tot_time + timedelta(seconds=diff)
    return tot_time


def main() -> None:
    parser = argparse.ArgumentParser(description="TOT Info - Display TOT timestamps for video")
    parser.add_argument("-i", "--input", required=True, help="Input TS file")
    parser.add_argument("-a", "--avs", required=False, help="AVS file with Trim() specifications (optional)")
    parser.add_argument("-o", "--output", help="Output JSON file (optional)")
    args = parser.parse_args()

    # 1. Analyze video first to know total frame count
    print("Analyzing video stream...")
    analyzer = StreamAnalyzer()
    video_pid = analyzer.find_video_pid(args.input)
    print(f"  Video PID: 0x{video_pid:x}")

    video_index = analyzer.build_video_index(args.input, video_pid)
    total_frames = len(video_index)
    print(f"  Total frames: {total_frames}")

    if total_frames == 0:
        print("Error: No video frames found.", file=sys.stderr)
        sys.exit(1)

    # 2. Determine ranges to process
    trim_specs: List[Tuple[int, int]] = []
    if args.avs:
        print(f"Parsing AVS file: {args.avs}")
        try:
            trim_specs = parse_avs_file(args.avs)
            if not trim_specs:
                print("Error: No Trim() found in AVS", file=sys.stderr)
                sys.exit(1)
            print(f"  Found {len(trim_specs)} trim ranges")
        except Exception as e:
            print(f"Error parsing AVS: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("No AVS file specified. Processing full video duration.")
        trim_specs = [(0, total_frames - 1)]

    # Display Service ID
    if analyzer.service_id:
        print(f"  Service ID: {analyzer.service_id} (0x{analyzer.service_id:x})")
    print()

    # 3. Get TOT timestamps for each range
    segments = []
    for i, (start_frame, end_frame) in enumerate(trim_specs):
        label = f"Segment {i+1}" if args.avs else "Full Video"
        print(f"{label}: frames [{start_frame}, {end_frame}]")

        if start_frame < 0 or end_frame >= total_frames:
            print(f"  Error: Frame index {end_frame} out of bounds.", file=sys.stderr)
            sys.exit(1)

        start_pts = video_index[start_frame].pts
        # Use next frame for end time to represent the duration correctly
        end_pts = video_index[end_frame + 1].pts if end_frame + 1 < total_frames else video_index[end_frame].pts

        start_tot = find_tot_near_frame(args.input, start_pts, video_pid)
        end_tot = find_tot_near_frame(args.input, end_pts, video_pid)

        if not start_tot or not end_tot:
            print("  Error: TOT not found.", file=sys.stderr)
            sys.exit(1)

        duration = round((end_tot - start_tot).total_seconds(), 3)
        s_str = f"{start_tot.strftime('%Y-%m-%d %H:%M:%S')}.{start_tot.microsecond // 1000:03d}"
        e_str = f"{end_tot.strftime('%Y-%m-%d %H:%M:%S')}.{end_tot.microsecond // 1000:03d}"

        print(f"  Start TOT: {s_str} JST")
        print(f"  End TOT:   {e_str} JST")
        print(f"  Duration:  {duration} seconds\n")

        segments.append({
            "index": i + 1, "frames": [start_frame, end_frame],
            "start_tot": s_str, "end_tot": e_str, "duration_sec": duration
        })

    # 4. JSON output
    if args.output:
        out_data = {"input_file": args.input, "avs_file": args.avs, "segments": segments}
        if analyzer.service_id: out_data["sid"] = analyzer.service_id
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(out_data, f, indent=2, ensure_ascii=False)
        print(f"JSON output written to: {args.output}")

if __name__ == "__main__":
    main()