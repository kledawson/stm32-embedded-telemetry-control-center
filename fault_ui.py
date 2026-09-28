"""Modeless fault controls for live firmware and the hardware-free demo source."""

from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer, QRectF
from PyQt6.QtGui import QColor, QPainter, QPen, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (QBoxLayout, QDialog, QFrame, QHBoxLayout, QLabel,
                             QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
                             QVBoxLayout, QWidget)

from dashboard_ui import STYLE, fit_window_to_screen
from sources import DemoWorker, SerialWorker


SCENARIOS = (
    ("checksum", "Bit flip + checksum", "One bit changes after a simulated packet's checksum is calculated. The parser rejects it."),
    ("watchdog", "Watchdog reset", "Simulated telemetry stops feeding the watchdog. The model reboots and packets return."),
    ("mutex", "UART mutex contention", "A simulated status task holds the UART lock. Packets wait and then resume."),
)
LIVE_SCENARIOS = (
    ("checksum", "Bit flip + checksum", "The board flips one bit in a real sensor packet after calculating CHK. The host rejects that packet."),
    ("watchdog", "Watchdog reset", "The telemetry task stops feeding IWDG. Hardware resets the board; the boot report verifies why."),
    ("mutex", "UART mutex contention", "The status task holds the UART mutex briefly. Telemetry waits, then resumes without mixed output."),
)


class MutexTimeline(QWidget):
    """Mutex hold markers and validated arrivals on one time axis."""

    def __init__(self):
        super().__init__()
        self.demo = False
        self.frames = ()
        self.hold_at = self.release_at = None
        self.stage = 2
        self.now = self.command_at = None
        self.setMinimumHeight(210)
        self.setAccessibleName("UART mutex ownership and validated packet timeline")

    def set_evidence(self, frames, hold_at, release_at, stage, now, command_at):
        evidence = (tuple(frames), hold_at, release_at, stage, now, command_at)
        previous = (self.frames, self.hold_at, self.release_at, self.stage,
                    self.now, self.command_at)
        if evidence != previous:
            (self.frames, self.hold_at, self.release_at, self.stage,
             self.now, self.command_at) = evidence
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0f1722"))
        if self.command_at is None and self.hold_at is None:
            painter.setPen(QColor("#a9bbcb"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Run the demo to capture UART mutex timing" if self.demo else
                             "Trigger on board to capture the UART mutex event")
            return
        width = self.width()
        left, right = (102 if width < 500 else 130), width - 24
        start_ms = -150
        hold_at = self.hold_at or self.command_at or self.now
        release_ms = ((self.release_at - hold_at) * 1000 if self.release_at else None)
        end_ms = max(700, ((int((release_ms or 350) + 420 + 99) // 100) * 100))
        position = lambda ms: left + (ms - start_ms) / (end_ms - start_ms) * (right - left)
        if self.stage == 0:
            playhead = min(end_ms, max(start_ms, (self.now - hold_at) * 1000))
        else:
            playhead = end_ms
        hold_x = position(0)
        bar_end = min(playhead, release_ms if release_ms is not None else playhead)
        show_hold = self.hold_at is not None and bar_end > 0
        release_visible = release_ms is not None and playhead >= release_ms
        painter.setPen(QColor("#91a7b9"))
        painter.drawText(8, 52, "MUTEX" if width < 500 else "StatusTask")
        painter.drawText(8, 117, "PACKETS" if width < 500 else "Valid demo" if self.demo else "Valid COM3")
        painter.drawText(QRectF(left, 6, right - left, 19), Qt.AlignmentFlag.AlignRight,
                         "LIVE CAPTURE" if self.stage == 0 else "CAPTURED RESULT")
        painter.setPen(QPen(QColor("#2b3b4c"), 1))
        for tick in range(-100, end_ms + 1, 100 if width >= 600 else 200):
            x = round(position(tick))
            painter.drawLine(x, 29, x, 154)
            painter.setPen(QColor("#92a8b8"))
            painter.drawText(QRectF(x - 24, 160, 48, 18), Qt.AlignmentFlag.AlignCenter, str(tick))
            painter.setPen(QPen(QColor("#2b3b4c"), 1))
        painter.drawLine(left, 53, right, 53)
        painter.drawLine(left, 114, right, 114)
        if show_hold:
            painter.fillRect(QRectF(hold_x, 101, max(2, position(bar_end) - hold_x), 26),
                             QColor(185, 133, 79, 35))
            bar = QRectF(hold_x, 39, max(2, position(bar_end) - hold_x), 28)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#b9854f"))
            painter.drawRoundedRect(bar, 5, 5)
            if bar.width() >= painter.fontMetrics().horizontalAdvance("MUTEX HELD") + 16:
                painter.save()
                painter.setClipRect(bar.adjusted(3, 0, -3, 0))
                painter.setPen(QColor("#101820"))
                painter.drawText(bar, Qt.AlignmentFlag.AlignCenter, "MUTEX HELD")
                painter.restore()
        elif self.stage == 0:
            painter.setPen(QColor("#a9bbcb"))
            painter.drawText(QRectF(left + 5, 38, right - left - 10, 30),
                             Qt.AlignmentFlag.AlignVCenter,
                             "Waiting for simulated HOLD" if self.demo else "Waiting for firmware HOLD marker")
        painter.setBrush(QColor("#55b7cd"))
        painter.setPen(Qt.PenStyle.NoPen)
        for stamp in self.frames:
            ms = (stamp - hold_at) * 1000
            if start_ms <= ms <= min(end_ms, playhead):
                painter.drawEllipse(QRectF(position(ms) - 3.5, 110.5, 7, 7))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if release_visible:
            release_x = position(release_ms)
            painter.setPen(QPen(QColor("#e5af73"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(round(release_x), 30, round(release_x), 151)
            painter.setPen(QColor("#d9b994"))
            painter.drawText(QRectF(min(release_x + 5, right - 65), 72, 65, 20),
                             Qt.AlignmentFlag.AlignLeft, "RELEASE")
        if self.stage < 2:
            painter.setPen(QPen(QColor("#55b7cd"), 1, Qt.PenStyle.DashLine))
            cursor_x = round(position(playhead))
            painter.drawLine(cursor_x, 29, cursor_x, 154)
        painter.setPen(QColor("#91a7b9"))
        painter.drawText(QRectF(left, 186, right - left, 18), Qt.AlignmentFlag.AlignCenter,
                         "Time from mutex HOLD marker (ms)" if self.hold_at else
                         "Waiting for simulated HOLD · dots are valid frames" if self.demo else
                         "Waiting for board marker · dots are valid COM3 frames")
        painter.end()


class ChecksumFlow(QWidget):
    """Animated explanation using the actual rejected and next accepted packets."""

    def __init__(self):
        super().__init__()
        self.packet = self.next_frame = None
        self.stage = 2
        self.progress = 1.0
        self.setMinimumHeight(240)
        self.setAccessibleName("Animated checksum packet journey")

    def set_evidence(self, packet, next_frame, stage, progress):
        evidence = (packet, next_frame, stage, progress)
        if evidence != (self.packet, self.next_frame, self.stage, self.progress):
            self.packet, self.next_frame, self.stage, self.progress = evidence
            self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        height = 420 if self.width() < 600 else 240
        if self.minimumHeight() != height:
            self.setMinimumHeight(height)

    @staticmethod
    def _label(painter, rect, text, color, bold=False):
        font = painter.font()
        font.setBold(bold)
        painter.setFont(font)
        painter.setPen(QColor(color))
        clipped = painter.fontMetrics().elidedText(str(text), Qt.TextElideMode.ElideRight,
                                                   max(0, round(rect.width())))
        painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, clipped)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0f1722"))
        width = self.width()
        narrow = width < 600
        margin = 14
        if narrow:
            cards = [QRectF(margin, 12 + index * 119, width - margin * 2, 96) for index in range(3)]
        else:
            gap = 32
            card_width = (width - margin * 2 - gap * 2) / 3
            cards = [QRectF(margin + index * (card_width + gap), 24, card_width, 145)
                     for index in range(3)]
        packet = self.packet if self.stage > 0 else None
        phase = self.progress if self.stage == 1 else 1.0 if self.stage == 2 else 0.0
        revealed = (packet is not None, packet is not None and phase >= .28,
                    packet is not None and phase >= .62)
        headings = ("1  SENSOR FRAME", "2  BIT FLIP ON UART", "3  HOST CHECK")
        colors = ("#55b7cd", "#e5af73", "#e4868e")
        for index, card in enumerate(cards):
            active = revealed[index]
            painter.setPen(QPen(QColor(colors[index] if active else "#34475a"), 1.5))
            painter.setBrush(QColor("#172a35" if active and index == 0 else
                                    "#332b22" if active and index == 1 else
                                    "#34252b" if active else "#141d29"))
            painter.drawRoundedRect(card, 8, 8)
            x, y, content_width = card.x() + 13, card.y(), card.width() - 26
            self._label(painter, QRectF(x, y + 9, content_width, 20), headings[index],
                        colors[index] if active else "#8297a8", True)
            if index == 0:
                primary = packet["before_ax"].replace("AX:", "AX: ", 1) if active else "Awaiting sensor packet"
                secondary = f"Original {packet['before_byte']}  ·  CHK {packet['sent_checksum']}" if active else "Board computes checksum"
            elif index == 1:
                primary = packet["after_ax"].replace("AX:", "AX: ", 1) if active else "Waiting for bit flip"
                secondary = f"{packet['before_byte']}  →  {packet['after_byte']}" if active else "One byte changes after CHK"
            else:
                primary = f"{packet['sent_checksum']}  ≠  {packet['calculated_checksum']}" if active else "Waiting for host check"
                secondary = "REJECTED · never graphed" if active else "Host recalculates checksum"
            self._label(painter, QRectF(x, y + 39, content_width, 25), primary,
                        "#f3f7fa" if active else "#8fa2b2", True)
            self._label(painter, QRectF(x, y + (70 if narrow else 81), content_width, 19), secondary,
                        colors[index] if active else "#8297a8")
        for index in (0, 1):
            first, second = cards[index], cards[index + 1]
            if narrow:
                x = width / 2
                y1, y2 = first.bottom() + 3, second.top() - 5
                painter.setPen(QPen(QColor("#5c7184"), 2))
                painter.drawLine(round(x), round(y1), round(x), round(y2))
                painter.drawLine(round(x), round(y2), round(x - 5), round(y2 - 5))
                painter.drawLine(round(x), round(y2), round(x + 5), round(y2 - 5))
            else:
                y = first.center().y()
                x1, x2 = first.right() + 3, second.left() - 5
                painter.setPen(QPen(QColor("#5c7184"), 2))
                painter.drawLine(round(x1), round(y), round(x2), round(y))
                painter.drawLine(round(x2), round(y), round(x2 - 5), round(y - 5))
                painter.drawLine(round(x2), round(y), round(x2 - 5), round(y + 5))
        if self.stage == 1 and packet:
            segment = min(1, phase * 2)
            first, second = cards[0], cards[1]
            x = width / 2 if narrow else first.right() + (second.left() - first.right()) * segment
            y = first.bottom() + (second.top() - first.bottom()) * segment if narrow else first.center().y()
            if phase > .5:
                first, second = cards[1], cards[2]
                segment = min(1, (phase - .5) * 2)
                x = width / 2 if narrow else first.right() + (second.left() - first.right()) * segment
                y = first.bottom() + (second.top() - first.bottom()) * segment if narrow else first.center().y()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#e5af73"))
            painter.drawEllipse(QRectF(x - 5, y - 5, 10, 10))
        footer_y = 374 if narrow else 190
        footer = QRectF(margin, footer_y, width - margin * 2, 32)
        accepted = self.stage == 2 and self.next_frame is not None
        painter.setPen(QPen(QColor("#378768" if accepted else "#34475a"), 1))
        painter.setBrush(QColor("#173629" if accepted else "#141d29"))
        painter.drawRoundedRect(footer, 6, 6)
        footer_text = (f"✓  NEXT VALID FRAME  ·  SEQ {self.next_frame['sequence']}  ·  {self.next_frame['ax']}  →  GRAPH"
                       if accepted else "Next valid packet can still pass to the graph")
        self._label(painter, footer.adjusted(10, 0, -10, 0), footer_text,
                    "#79d7a6" if accepted else "#91a7b9", accepted)
        painter.end()


class WatchdogSequence(QWidget):
    """Show the observed task stall, hardware reset, and verified fresh frames."""

    def __init__(self):
        super().__init__()
        self.demo = False
        self.phase = "idle"
        self.live = False
        self.reset_reason = None
        self.frame_age = None
        self.elapsed = 0.0
        self.setMinimumHeight(160)
        self.setAccessibleName("Watchdog supervision and reboot sequence")

    def set_evidence(self, phase, live, reset_reason, frame_age, elapsed):
        # Only the active fault uses time-based motion. A ready or completed
        # sequence must stay visually still between refreshes.
        if phase not in ("armed", "stalled", "recovering", "failed"):
            elapsed = 0.0
        evidence = (phase, live, reset_reason, frame_age, elapsed)
        if evidence != (self.phase, self.live, self.reset_reason, self.frame_age, self.elapsed):
            self.phase, self.live, self.reset_reason, self.frame_age, self.elapsed = evidence
            self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        height = 340 if self.width() < 600 else 160
        if self.minimumHeight() != height:
            self.setMinimumHeight(height)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0f1722"))
        width = self.width()
        narrow = width < 600
        margin, gap = 14, 28
        if narrow:
            cards = [QRectF(margin, 8 + index * 105, width - 2 * margin, 84)
                     for index in range(3)]
        else:
            card_width = (width - 2 * margin - 2 * gap) / 3
            cards = [QRectF(margin + index * (card_width + gap), 16, card_width, 116)
                     for index in range(3)]
        rebooting = self.phase in ("armed", "stalled", "recovering") and not self.live
        verified = self.phase == "recovered" and self.reset_reason == "IWDG"
        failed = self.phase == "failed"
        faulted = self.phase in ("armed", "stalled", "recovering", "failed")
        task = ("Recovery unverified" if failed else "Fresh frames" if verified else "No new frames" if rebooting or self.phase == "stalled" else
                "Feed stopped" if faulted else "Telemetry flowing")
        watchdog = ("Response missing" if failed else "Reset: IWDG" if verified else "Hardware reset" if rebooting else
                    "Timer running" if faulted else "Fed normally")
        mcu = ("Check board" if failed else "Boot verified" if verified else "Reconnecting" if rebooting else
               "Reset pending" if self.phase == "stalled" else "Running")
        details = (
            "Check connection and firmware" if failed else ("Valid demo frames" if self.demo else "Valid COM3 again") if verified else
            f"{self.frame_age:.1f} s since frame" if self.frame_age is not None and faulted else
            "Task feeds IWDG",
            "Expected marker not seen" if failed else ("Simulated reset confirmed" if self.demo else "Boot report confirms cause") if verified else
            "Board restarts itself" if rebooting else
            "No software reset command" if faulted else "Supervision active",
            "No verified reboot" if failed else "Sensor data restored" if verified else
            ("Waiting for demo frames" if self.demo else "Waiting for serial port") if rebooting else
            "Watch live graph below" if faulted else "Board healthy",
        )
        colors = ("#79d7a6" if verified else "#e4868e" if faulted else "#55b7cd",
                  "#79d7a6" if verified else "#e4868e" if failed else "#e5af73" if faulted else "#55b7cd",
                  "#79d7a6" if verified else "#e4868e" if failed else "#e5af73" if rebooting else "#8fa2b2")
        for index, card in enumerate(cards):
            painter.setPen(QPen(QColor(colors[index]), 1.3))
            painter.setBrush(QColor("#172a35" if not faulted else
                                    "#34252b" if index == 0 and not verified else
                                    "#332b22" if not verified else "#173629"))
            painter.drawRoundedRect(card, 8, 8)
            x, y, content_width = card.x() + 12, card.y(), card.width() - 24
            ChecksumFlow._label(painter, QRectF(x, y + 7, content_width, 19),
                                ("TELEMETRY TASK", "INDEPENDENT WATCHDOG", "SIM MCU / DATA" if self.demo else "MCU / COM3")[index],
                                colors[index], True)
            ChecksumFlow._label(painter, QRectF(x, y + 31, content_width, 24),
                                (task, watchdog, mcu)[index], "#f3f7fa", True)
            ChecksumFlow._label(painter, QRectF(x, y + (60 if narrow else 75), content_width, 19),
                                details[index], "#b2c4d1")
        painter.setPen(QPen(QColor("#657b8b"), 2))
        for index in (0, 1):
            first, second = cards[index], cards[index + 1]
            if narrow:
                x, y1, y2 = width / 2, first.bottom() + 2, second.top() - 4
                painter.drawLine(round(x), round(y1), round(x), round(y2))
                painter.drawLine(round(x), round(y2), round(x - 5), round(y2 - 5))
                painter.drawLine(round(x), round(y2), round(x + 5), round(y2 - 5))
            else:
                y, x1, x2 = first.center().y(), first.right() + 2, second.left() - 4
                painter.drawLine(round(x1), round(y), round(x2), round(y))
                painter.drawLine(round(x2), round(y), round(x2 - 5), round(y - 5))
                painter.drawLine(round(x2), round(y), round(x2 - 5), round(y + 5))
        if faulted and not verified:
            # Indeterminate motion signals hardware activity; it is not a
            # fabricated countdown to a firmware timeout.
            ring = QRectF(cards[1].right() - 30, cards[1].top() + 12, 16, 16)
            painter.setPen(QPen(QColor("#e5af73"), 2))
            painter.drawArc(ring, int((self.elapsed * 300 % 360) * 16), 105 * 16)
        painter.end()


class WatchdogGap(QWidget):
    """Validated arrivals across a watchdog reset, retained across COM reconnect."""

    def __init__(self):
        super().__init__()
        self.demo = False
        self.frames = ()
        self.command_at = self.last_before = self.stale_at = None
        self.reboot_at = self.first_return_at = self.recovered_at = None
        self.now = None
        self.tick_step_s = 1.0
        self.setMinimumHeight(160)
        self.setAccessibleName("Validated COM3 packet gap during watchdog reset")

    def set_evidence(self, frames, command_at, last_before, stale_at, reboot_at,
                     first_return_at, recovered_at, now):
        evidence = (tuple(frames), command_at, last_before, stale_at, reboot_at,
                    first_return_at, recovered_at, now)
        previous = (self.frames, self.command_at, self.last_before, self.stale_at,
                    self.reboot_at, self.first_return_at, self.recovered_at, self.now)
        if evidence != previous:
            (self.frames, self.command_at, self.last_before, self.stale_at,
             self.reboot_at, self.first_return_at, self.recovered_at, self.now) = evidence
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0f1722"))
        if self.command_at is None:
            painter.setPen(QColor("#9eb3c2"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Run the demo to capture the packet interruption" if self.demo else
                             "Trigger the watchdog to capture the packet interruption")
            return
        width = self.width()
        left, right = (76 if width < 500 else 98), width - 28
        elapsed = max(0.0, self.now - self.command_at)
        gap_start = ((self.last_before - self.command_at) if self.last_before else None)
        gap_end = ((self.first_return_at - self.command_at) if self.first_return_at else elapsed)
        captured = self.last_before is not None and self.first_return_at is not None
        if captured:
            # The real IWDG silence is several seconds long. Show the two
            # packet clusters at millisecond scale and explicitly break time
            # across the gap instead of letting the gap consume the plot.
            span = right - left
            before_edge = left + span * .35
            after_edge = left + span * .59
            before_start = self.last_before - .45
            after_end = self.first_return_at + .65

            def position(stamp):
                absolute = self.command_at + stamp
                if absolute <= self.last_before:
                    return left + (absolute - before_start) / .45 * (before_edge - left)
                if absolute >= self.first_return_at:
                    return after_edge + (absolute - self.first_return_at) / .65 * (right - after_edge)
                return before_edge + (absolute - self.last_before) / (self.first_return_at - self.last_before) * (after_edge - before_edge)

            visible = lambda stamp: before_start <= stamp <= self.last_before or self.first_return_at <= stamp <= after_end
        else:
            start = -.5
            end = max(3.0, (self.recovered_at - self.command_at + .75) if self.recovered_at else elapsed + .35)
            position = lambda stamp: left + (stamp - start) / (end - start) * (right - left)
            visible = lambda stamp: start <= stamp <= end
        painter.setPen(QColor("#91a7b9"))
        painter.drawText(8, 77, "FRAMES" if width < 500 else "Valid demo" if self.demo else "Valid COM3")
        painter.setPen(QPen(QColor("#2b3b4c"), 1))
        if captured:
            ticks = [(-400, "−400"), (-200, "−200"), (0, "LAST")]
            ticks += [(0, "FIRST"), (200, "+200"), (400, "+400"), (600, "+600")]
            if width < 500:
                ticks = [(-400, "−400"), (0, "LAST"), (0, "FIRST"), (400, "+400")]
            for index, (milliseconds, label) in enumerate(ticks):
                x = (before_edge + milliseconds / 450 * (before_edge - left)
                     if index < (3 if width >= 500 else 2) else
                     after_edge + milliseconds / 650 * (right - after_edge))
                painter.drawLine(round(x), 27, round(x), 104)
                painter.setPen(QColor("#91a7b9"))
                painter.drawText(QRectF(x - 24, 110, 48, 18), Qt.AlignmentFlag.AlignCenter, label)
                painter.setPen(QPen(QColor("#2b3b4c"), 1))
        else:
            tick = 0.0
            while tick <= end:
                x = round(position(tick))
                painter.drawLine(x, 27, x, 104)
                painter.setPen(QColor("#91a7b9"))
                painter.drawText(QRectF(x - 22, 110, 44, 18), Qt.AlignmentFlag.AlignCenter,
                                 f"{tick:.0f}")
                painter.setPen(QPen(QColor("#2b3b4c"), 1))
                tick += self.tick_step_s
        painter.drawLine(left, 75, right, 75)
        if gap_start is not None and gap_end > gap_start:
            x1, x2 = position(gap_start), position(gap_end)
            painter.fillRect(QRectF(x1, 58, max(2, x2 - x1), 34), QColor(185, 93, 105, 35))
            if captured:
                painter.setPen(QPen(QColor("#e4868e"), 2))
                for edge in (x1 + 5, x2 - 5):
                    painter.drawLine(round(edge - 4), 69, round(edge + 2), 81)
                    painter.drawLine(round(edge + 2), 69, round(edge + 8), 81)
                ChecksumFlow._label(painter, QRectF(x1 + 8, 33, x2 - x1 - 16, 22),
                                    f"{gap_end - gap_start:.1f} s · NO PACKETS", "#e4868e", True)
            elif x2 - x1 > 110:
                ChecksumFlow._label(painter, QRectF(x1 + 9, 34, x2 - x1 - 18, 20),
                                    f"{gap_end - gap_start:.1f} s  ·  NO VALID PACKETS" if self.first_return_at else
                                    "NO VALID PACKETS", "#e4868e", True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#55b7cd"))
        for stamp in self.frames:
            offset = stamp - self.command_at
            if visible(stamp):
                painter.drawEllipse(QRectF(position(offset) - 4, 71, 8, 8))
        if self.reboot_at is not None:
            x = round(position(self.reboot_at - self.command_at))
            painter.setPen(QPen(QColor("#e5af73"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(x, 26, x, 101)
            ChecksumFlow._label(painter, QRectF(min(x + 5, right - 85), 7, 85, 18),
                                "RESTART" if self.demo else "PORT LOST", "#e5af73", True)
        if self.first_return_at is not None:
            x = round(position(self.first_return_at - self.command_at))
            painter.setPen(QPen(QColor("#79d7a6"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(x, 25, x, 101)
            ChecksumFlow._label(painter, QRectF(min(x + 5, right - 88), 88, 88, 18),
                                "FRESH FRAME", "#79d7a6", True)
        painter.setPen(QColor("#91a7b9"))
        painter.drawText(QRectF(left, 128, right - left, 18), Qt.AlignmentFlag.AlignCenter,
                         "Milliseconds around packet clusters · gap time compressed" if captured else
                         "Seconds since fault command · dots are validated packets")
        painter.end()


class FaultLabDialog(QDialog):
    def __init__(self, dashboard):
        super().__init__(dashboard)
        self.dashboard = dashboard
        self.selected_scenario = "checksum"
        self.presentation_kind = None
        self.presentation_demo = False
        self.presentation_stage = 2
        self.presentation_started_at = 0.0
        self.presentation_stage_at = 0.0
        self.presentation_tick = 0
        self.mutex_frozen_frames = ()
        self.presentation_timer = QTimer(self)
        self.presentation_timer.setInterval(50)
        self.presentation_timer.timeout.connect(self._advance_presentation)
        self.setObjectName("faultDialog")
        self.setWindowTitle("Fault Injection")
        fit_window_to_screen(self, (1140, 770), (480, 460), dashboard)
        self.setStyleSheet(STYLE + """
            QDialog#faultDialog, QWidget#faultRoot, QScrollArea#faultScroll { background: #0b0e14; }
            QFrame#faultCard, QFrame#faultRail { background: #151c28; border: 1px solid #2c3748; border-radius: 8px; }
            QFrame#faultBanner { background: #16241f; border: 1px solid #378768; border-radius: 7px; }
            QFrame#faultBanner[tone="warn"] { background: #2a241a; border-color: #ab8340; }
            QFrame#faultBanner[tone="bad"] { background: #2a1c22; border-color: #a45c69; }
            QPushButton#faultChoice { text-align: left; padding: 9px 11px; }
            QPushButton#faultChoice:checked { background: #1d5264; border-color: #55b7cd; }
            QLabel#faultTitle { font-size: 19px; font-weight: 600; }
            QLabel#faultState { font-size: 16px; font-weight: 600; }
            QLabel#faultMuted { color: #a9bbcb; }
            QLabel#faultStep { background: #111721; border-radius: 4px; color: #91a7b9; padding: 6px; }
            QLabel#faultStep[active="true"] { background: #1d5264; color: #edf3fa; }
            QLabel#faultProofStatus { color: #79d7a6; }
            QFrame#faultMetricPrimary { background: #142d37; border: 1px solid #3b8294; border-radius: 7px; }
            QFrame#faultMetricSecondary { background: #2b261f; border: 1px solid #98734d; border-radius: 7px; }
            QLabel#faultPrimaryValue, QLabel#faultMetricValue {
                color: #f1f7fa; font-size: 25px; font-weight: 700;
            }
            QPlainTextEdit#faultEvents { background: #0f151e; border: 1px solid #2c3748;
                border-radius: 5px; color: #b7c9d9; font-family: Consolas; font-size: 11px; }
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        header = QHBoxLayout()
        header.addWidget(self._label("Fault Injection", "faultTitle"))
        header.addStretch()
        self.source_label = self._label("CHOOSE A SOURCE", "section")
        header.addWidget(self.source_label)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        header.addWidget(close)
        outer.addLayout(header)

        scroll = QScrollArea()
        scroll.setObjectName("faultScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll = scroll
        outer.addWidget(scroll, 1)
        body = QWidget()
        body.setObjectName("faultRoot")
        scroll.setWidget(body)
        self.content_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, body)
        self.content_layout.setContentsMargins(2, 2, 8, 8)
        self.content_layout.setSpacing(10)

        rail = self._card("faultRail")
        self.rail = rail
        rail.setMaximumWidth(238)
        rail_box = QVBoxLayout(rail)
        rail_box.setContentsMargins(11, 11, 11, 11)
        rail_box.setSpacing(6)
        rail_box.addWidget(self._label("CHOOSE A FAILURE", "section"))
        self.scenario_buttons = {}
        for key, title, _ in LIVE_SCENARIOS:
            button = QPushButton(title)
            button.setObjectName("faultChoice")
            button.setCheckable(True)
            button.setChecked(key == self.selected_scenario)
            button.clicked.connect(lambda checked=False, selected=key: self.select_scenario(selected))
            rail_box.addWidget(button)
            self.scenario_buttons[key] = button
        rail_box.addStretch()
        self.source_note = self._label("Connect demo data or a board to begin.", "faultMuted")
        self.source_note.setWordWrap(True)
        rail_box.addWidget(self.source_note)
        self.content_layout.addWidget(rail)

        right = QWidget()
        right_box = QVBoxLayout(right)
        right_box.setContentsMargins(0, 0, 0, 0)
        right_box.setSpacing(9)
        self.content_layout.addWidget(right, 1)

        title_row = QHBoxLayout()
        title_column = QVBoxLayout()
        title_column.setSpacing(2)
        self.scenario_title = self._label("Corrupt frame", "panelTitle")
        self.scenario_description = self._label("", "faultMuted")
        self.scenario_description.setWordWrap(True)
        title_column.addWidget(self.scenario_title)
        title_column.addWidget(self.scenario_description)
        title_row.addLayout(title_column, 1)
        right_box.addLayout(title_row)

        controls = QHBoxLayout()
        controls.setSpacing(7)
        self.start_button = QPushButton("Start demo source")
        self.start_button.clicked.connect(self.dashboard.start_demo_for_fault_lab)
        self.inject_button = QPushButton("Inject fault")
        self.inject_button.setObjectName("primary")
        self.inject_button.clicked.connect(self.inject_selected)
        controls.addWidget(self.start_button)
        controls.addWidget(self.inject_button)
        controls.addStretch()
        right_box.addLayout(controls)

        banner = self._card("faultBanner")
        self.banner = banner
        banner_box = QVBoxLayout(banner)
        banner_box.setContentsMargins(12, 9, 12, 9)
        banner_box.setSpacing(2)
        self.state_label = self._label("Demo source ready", "faultState")
        self.state_label.setWordWrap(True)
        self.state_detail = self._label("Choose a failure and inject it.", "faultMuted")
        self.state_detail.setWordWrap(True)
        banner_box.addWidget(self.state_label)
        banner_box.addWidget(self.state_detail)
        right_box.addWidget(banner)

        steps = QHBoxLayout()
        self.steps_layout = steps
        steps.setSpacing(5)
        self.step_labels = []
        for text in ("1  Running", "2  Fault visible", "3  Recovered"):
            label = self._label(text, "faultStep")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            steps.addWidget(label, 1)
            self.step_labels.append(label)
        right_box.addLayout(steps)

        self.proof_card = self._card("faultCard")
        self.proof_card.setMinimumHeight(210)
        proof_box = QVBoxLayout(self.proof_card)
        proof_box.setContentsMargins(10, 9, 10, 9)
        proof_box.setSpacing(7)
        proof_head = QHBoxLayout()
        self.proof_head = proof_head
        self.proof_heading = self._label("CAPTURED COM3 EVIDENCE", "section")
        self.proof_status = self._label("", "faultProofStatus")
        proof_head.addWidget(self.proof_heading)
        proof_head.addStretch()
        proof_head.addWidget(self.proof_status)
        proof_box.addLayout(proof_head)
        self.checksum_visual = ChecksumFlow()
        proof_box.addWidget(self.checksum_visual)
        self.mutex_metrics = QWidget()
        metrics_layout = QHBoxLayout(self.mutex_metrics)
        metrics_layout.setContentsMargins(0, 0, 0, 0)
        metrics_layout.setSpacing(8)
        self.mutex_metric_values = {}
        self.mutex_metric_notes = {}
        for key, caption, note, name in (
            ("gap", "PACKET GAP", "Time between valid COM3 frames", "faultMetricPrimary"),
            ("hold", "MUTEX HELD", "Firmware lock ownership", "faultMetricSecondary"),
        ):
            metric = self._card(name)
            metric_box = QVBoxLayout(metric)
            metric_box.setContentsMargins(12, 7, 12, 7)
            metric_box.setSpacing(2)
            metric_box.addWidget(self._label(caption, "section"))
            value = self._label("—", "faultPrimaryValue" if key == "gap" else "faultMetricValue")
            metric_box.addWidget(value)
            detail = self._label(note, "faultMuted")
            detail.setWordWrap(True)
            metric_box.addWidget(detail)
            self.mutex_metric_values[key] = value
            self.mutex_metric_notes[key] = detail
            metrics_layout.addWidget(metric, 1)
        proof_box.addWidget(self.mutex_metrics)
        self.mutex_metrics.hide()
        self.mutex_timeline = MutexTimeline()
        proof_box.addWidget(self.mutex_timeline)
        self.proof_result = self._label("", "faultProofStatus")
        self.proof_result.setWordWrap(True)
        proof_box.addWidget(self.proof_result)
        self.proof_note = self._label("Captured events are held on screen for viewing; the COM3 stream keeps its real timing.", "faultMuted")
        self.proof_note.setWordWrap(True)
        proof_box.addWidget(self.proof_note)

        right_box.addWidget(self.proof_card)
        graph_card = self._card("faultCard")
        self.graph_card = graph_card
        graph_box = QVBoxLayout(graph_card)
        graph_box.setContentsMargins(10, 9, 10, 9)
        self.graph_heading = self._label("LIVE TELEMETRY · ACCELERATION X", "section")
        graph_box.addWidget(self.graph_heading)
        self.watchdog_visual = WatchdogSequence()
        graph_box.addWidget(self.watchdog_visual)
        self.watchdog_visual.hide()
        self.watchdog_gap = WatchdogGap()
        graph_box.addWidget(self.watchdog_gap)
        self.watchdog_gap.hide()
        right_box.addWidget(graph_card)

        health_card = self._card("faultCard")
        self.health_card = health_card
        health_box = QVBoxLayout(health_card)
        health_box.setContentsMargins(10, 7, 10, 7)
        health_box.setSpacing(4)
        self.health_heading = self._label("LIVE HEALTH", "section")
        health_box.addWidget(self.health_heading)
        self.health_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.health_layout.setSpacing(7)
        self.health_values = {}
        for key, caption in (("link", "LINK"), ("sensor", "SENSOR"), ("age", "LAST FRAME"),
                             ("rejected", "REJECTED FRAMES"), ("reset", "RESET REASON")):
            metric = QWidget()
            metric_box = QVBoxLayout(metric)
            metric_box.setContentsMargins(2, 1, 2, 1)
            metric_box.setSpacing(2)
            metric_box.addWidget(self._label(caption, "faultMuted"))
            value = self._label("—", "healthValue")
            metric_box.addWidget(value)
            self.health_layout.addWidget(metric, 1)
            self.health_values[key] = value
        health_box.addLayout(self.health_layout)
        right_box.addWidget(health_card)

        events_card = self._card("faultCard")
        self.events_card = events_card
        events_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        events_box = QVBoxLayout(events_card)
        events_box.setContentsMargins(10, 9, 10, 9)
        event_head = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.event_head = event_head
        event_head.addWidget(self._label("DETECTION & RECOVERY", "section"))
        event_head.addStretch()
        event_legend = self._label(
            '<span style="color:#55b7cd">● command</span>  '
            '<span style="color:#e5af73">● fault</span>  '
            '<span style="color:#79d7a6">● recovery</span>', "faultMuted")
        event_legend.setTextFormat(Qt.TextFormat.RichText)
        event_legend.setStyleSheet("font-size: 11px;")
        event_head.addWidget(event_legend)
        events_box.addLayout(event_head)
        self.events = QPlainTextEdit()
        self.events.setObjectName("faultEvents")
        self.events.setReadOnly(True)
        self.events.document().setMaximumBlockCount(80)
        self.events.setMinimumHeight(94)
        self.events.setMaximumHeight(115)
        events_box.addWidget(self.events)
        right_box.addWidget(events_card)
        # Keep cards at their useful heights; extra window height belongs below
        # the content, not between the log heading and its text.
        right_box.addStretch(1)
        self.record_event("Fault Injection ready · connect demo data or a board")
        self.refresh()

    @staticmethod
    def _card(name):
        card = QFrame()
        card.setObjectName(name)
        return card

    @staticmethod
    def _label(text, name):
        label = QLabel(text)
        label.setObjectName(name)
        return label

    def select_scenario(self, scenario):
        if (self.dashboard.demo_fault_phase in ("active", "rebooting", "recovering") or
                self.dashboard.live_fault_phase in ("sent", "armed", "stalled", "contending", "recovering") or
                self.presentation_kind is not None and self.presentation_stage < 2):
            return
        self.presentation_kind = None
        self.selected_scenario = scenario
        self._refresh_without_scroll_jump()

    def _refresh_without_scroll_jump(self):
        scrollbar = self.scroll.verticalScrollBar()
        position = scrollbar.value()
        self.refresh()
        QTimer.singleShot(0, lambda: scrollbar.setValue(min(position, scrollbar.maximum())))

    def inject_selected(self):
        scrollbar = self.scroll.verticalScrollBar()
        position = scrollbar.value()
        if isinstance(self.dashboard.worker, SerialWorker):
            if self.dashboard.inject_live_fault(self.selected_scenario):
                self._begin_presentation(self.selected_scenario, demo=False)
        else:
            if self.dashboard.inject_demo_fault(self.selected_scenario):
                self._begin_presentation(self.selected_scenario, demo=True)
        QTimer.singleShot(0, lambda: scrollbar.setValue(min(position, scrollbar.maximum())))

    def _begin_presentation(self, kind, demo=False):
        self.presentation_kind = kind
        self.presentation_demo = demo
        if kind == "mutex":
            self.mutex_frozen_frames = ()
        self.presentation_stage = 1 if kind == "watchdog" else 0
        self.presentation_started_at = self.presentation_stage_at = time.monotonic()
        self.presentation_tick = 0
        self.presentation_timer.start()
        self.refresh()

    def _advance_presentation(self):
        if self.presentation_kind is None:
            self.presentation_timer.stop()
            return
        dashboard = self.dashboard
        prefix = "demo_fault_" if self.presentation_demo else "live_fault_"
        phase = getattr(dashboard, prefix + "phase")
        kind = getattr(dashboard, prefix + "kind")
        recovered_at = getattr(dashboard, prefix + "recovered_at")
        if phase == "failed" or kind != self.presentation_kind:
            self.presentation_stage = 2
            self.presentation_timer.stop()
        elif self.presentation_kind == "watchdog":
            if (phase == "recovered" and recovered_at is not None and
                    time.monotonic() - recovered_at >= 1.0):
                self.presentation_stage = 2
                self.presentation_timer.stop()
        elif self.presentation_stage == 0:
            captured = (getattr(dashboard, prefix + "packet") is not None if self.presentation_kind == "checksum" else
                        getattr(dashboard, prefix + "gap_ms") is not None)
            if captured and time.monotonic() - self.presentation_stage_at >= .7:
                if self.presentation_kind == "mutex":
                    self.mutex_frozen_frames = tuple(getattr(dashboard, prefix + "frame_times"))
                self.presentation_stage = 1
                self.presentation_stage_at = time.monotonic()
        elif self.presentation_stage == 1 and time.monotonic() - self.presentation_stage_at >= (
                1.6 if self.presentation_kind == "mutex" else 2.4):
            self.presentation_stage = 2
            self.presentation_timer.stop()
        self.presentation_tick += 1
        if self.isVisible():
            self.refresh()

    def _refresh_proof(self):
        kind = self.selected_scenario
        dashboard = self.dashboard
        prefix = "demo_fault_" if self.presentation_demo else "live_fault_"
        evidence = lambda name: getattr(dashboard, prefix + name)
        active = self.presentation_kind == kind and self.presentation_stage < 2
        stage = self.presentation_stage if active else 2
        if kind == "checksum":
            packet = evidence("packet") if evidence("kind") == kind else None
            next_frame = evidence("next_frame") if evidence("kind") == kind else None
            progress = min(1.0, (time.monotonic() - self.presentation_stage_at) / 2.4) if active and stage == 1 else 1.0
            self.proof_heading.setText("ONE SIMULATED PACKET · BIT FLIP" if self.presentation_demo else
                                       "ONE REAL PACKET · BIT FLIP")
            self.proof_status.setText(("Collecting" + "." * (self.presentation_tick % 4)) if stage == 0 else
                                      "REPLAYING CAPTURE" if stage == 1 and progress < .62 else
                                      "NEXT ACCEPTED" if stage == 2 and next_frame else
                                      "AWAITING TEST" if packet is None else "FRAME REJECTED")
            self.checksum_visual.set_evidence(packet, next_frame, stage, progress)
            self.checksum_visual.show()
            self.mutex_metrics.hide()
            self.proof_result.setText("")
            self.proof_note.setText("CHK is computed before the bit flip. The rejected frame never reaches the graph; the next valid one does.")
            self.mutex_timeline.hide()
        else:
            gap = evidence("gap_ms") if evidence("kind") == kind else None
            hold_at = evidence("hold_at") if evidence("kind") == kind else None
            release_at = evidence("release_at") if evidence("kind") == kind else None
            now = time.monotonic()
            gap_start = evidence("gap_start") if evidence("kind") == kind else None
            self.proof_heading.setText("SHARED UART · TASK TIMELINE")
            self.mutex_metric_notes["gap"].setText("Time between valid demo frames" if self.presentation_demo else
                                                   "Time between valid COM3 frames")
            self.mutex_metric_notes["hold"].setText("Simulated lock ownership" if self.presentation_demo else
                                                    "Firmware lock ownership")
            self.proof_status.setText("LIVE CAPTURE" if stage == 0 and self.presentation_kind == kind else
                                      "RESULT CAPTURED" if stage == 1 and gap is not None else
                                      "STREAM RESUMED" if gap is not None else "AWAITING TEST")
            self.mutex_metric_values["gap"].setText(
                f"{gap:.0f} ms" if gap is not None else
                f"{max(0, now - gap_start) * 1000:.0f} ms" if gap_start is not None and stage == 0 else "—")
            self.mutex_metric_values["hold"].setText(
                f"{(release_at - hold_at) * 1000:.0f} ms" if hold_at is not None and release_at is not None else
                f"{max(0, now - hold_at) * 1000:.0f} ms" if hold_at is not None and stage == 0 else "—")
            self.mutex_metrics.show()
            self.proof_result.setText("")
            self.proof_note.setText("Orange = simulated mutex hold · blue dots = validated demo packets." if self.presentation_demo else
                                    "Orange = firmware mutex hold · blue dots = validated COM3 frames. The captured result stays on screen; the board keeps streaming.")
            self.checksum_visual.hide()
            self.mutex_timeline.show()
            live_capture = active and stage == 0
            frames = (evidence("frame_times") if live_capture else
                      self.mutex_frozen_frames if self.mutex_frozen_frames else evidence("frame_times"))
            self.mutex_timeline.set_evidence(frames, hold_at, release_at,
                                             0 if live_capture else 2, now if live_capture else release_at,
                                             evidence("started_at") if evidence("kind") == kind else None)

    def record_event(self, message):
        lower = message.lower()
        if any(word in lower for word in ("recovered", "resumed", "released", "accepted",
                                           "confirmed", "connected", "verified", "rebooted",
                                           "boot report")):
            color = "#79d7a6"
        elif any(word in lower for word in ("rejected", "failed", "stale", "stopped",
                                             "starve", "hold", "lost", "dropped",
                                             "mismatch", "error", "resetting", "injected",
                                             "unconfirmed", "corrupt")):
            color = "#e5af73"
        else:
            color = "#55b7cd"
        cursor = QTextCursor(self.events.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.events.document().isEmpty():
            cursor.insertBlock()
        stamp_format = QTextCharFormat()
        stamp_format.setForeground(QColor("#7890a1"))
        event_format = QTextCharFormat()
        event_format.setForeground(QColor(color))
        cursor.insertText(f"{time.strftime('%H:%M:%S')}  ", stamp_format)
        cursor.insertText("●  " + message, event_format)
        scrollbar = self.events.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def refresh(self):
        dashboard = self.dashboard
        worker = dashboard.worker
        live = isinstance(worker, SerialWorker) and worker.isRunning()
        if live or dashboard.live_fault_kind is not None:
            self._refresh_faults(live, demo=False)
            return
        self._refresh_faults(False, demo=True)

    def _refresh_faults(self, live, demo=False):
        dashboard = self.dashboard
        demo_connected = demo and isinstance(dashboard.worker, DemoWorker) and dashboard.worker.isRunning()
        self.presentation_demo = demo
        self.mutex_timeline.demo = demo
        self.watchdog_visual.demo = demo
        self.watchdog_gap.demo = demo
        prefix = "demo_fault_" if demo else "live_fault_"
        evidence = lambda name: getattr(dashboard, prefix + name)
        phase, fault = evidence("phase"), evidence("kind")
        visual_phase = phase
        if demo and phase == "active":
            stale = dashboard.last_sample_at is not None and time.monotonic() - dashboard.last_sample_at > .55
            visual_phase = "contending" if fault == "mutex" else "stalled" if fault == "watchdog" and stale else "armed"
        elif demo and phase == "rebooting":
            visual_phase = "recovering"
        if self.selected_scenario not in {item[0] for item in LIVE_SCENARIOS}:
            self.selected_scenario = "checksum"
        scenarios = SCENARIOS if demo else LIVE_SCENARIOS
        selected = next(item for item in scenarios if item[0] == self.selected_scenario)
        if demo:
            source = "DEMO · SIMULATED DATA" if demo_connected else "REPLAY · READ ONLY" if dashboard.is_replay_mode else "NO SOURCE"
            note = ("Same three fault flows, generated locally. No board command is sent." if demo_connected else
                    "Connect demo data or select COM3 in the main window.")
        else:
            source = (f"{dashboard.port_combo.currentText()} · LIVE BOARD" if live else
                      "BOARD RECONNECTING" if dashboard.live_fault_reconnect_port else "BOARD OFFLINE")
            note = "These one-shot commands act on the connected firmware. Results come from real COM telemetry."
        self.source_label.setText(source)
        self.source_note.setText(note)
        self.scenario_title.setText(selected[1])
        self.scenario_description.setText(selected[2])
        animating = self.presentation_kind == self.selected_scenario and self.presentation_stage < 2
        presenting = animating and self.selected_scenario in ("checksum", "mutex")
        busy = phase in (("active", "rebooting", "recovering") if demo else
                         ("sent", "armed", "stalled", "contending", "recovering")) or animating
        live_titles = {key: title for key, title, _ in scenarios}
        for key, button in self.scenario_buttons.items():
            button.setVisible(key in live_titles)
            if key in live_titles:
                button.setText(live_titles[key])
            button.setChecked(key == self.selected_scenario)
            button.setEnabled(not busy)
        self.start_button.setVisible(demo and dashboard.worker is None)
        self.inject_button.setText("Run demo fault" if demo else "Trigger on board")
        recent = dashboard.last_sample_at is not None and time.monotonic() - dashboard.last_sample_at < 1.5
        self.inject_button.setEnabled((live or demo_connected) and recent and not dashboard.paused and not busy)
        self.proof_card.setVisible(self.selected_scenario in ("checksum", "mutex"))
        self.graph_card.setVisible(self.selected_scenario == "watchdog")
        self.watchdog_visual.setVisible(self.selected_scenario == "watchdog")
        self.watchdog_gap.setVisible(self.selected_scenario == "watchdog")
        self.graph_heading.setText("WATCHDOG EFFECT · VALIDATED PACKET GAP")
        if self.proof_card.isVisible():
            self._refresh_proof()
        if phase == "idle":
            title = ("Demo telemetry ready" if demo_connected else "Start the demo source" if demo else
                     "Live telemetry ready")
            detail = ("Choose a test and run the simulated fault." if demo_connected else
                      "Connect demo data to run the three fault scenarios." if demo else
                      "Choose a test and trigger a one-shot board command.")
            tone = "good" if live or demo_connected else "warn"
        elif phase == "sent":
            title, detail, tone = "Waiting for firmware", evidence("detail"), "warn"
        elif visual_phase in ("armed", "contending", "stalled"):
            title = {"checksum": "Bit flip armed", "watchdog": "Watchdog feed stopped",
                     "mutex": "UART mutex held"}.get(fault, "Fault active")
            detail, tone = evidence("detail"), "warn"
        elif visual_phase == "recovering":
            title, detail, tone = "Fault detected · verifying recovery", evidence("detail"), "warn"
        elif phase == "recovered":
            title = ("Simulated reboot complete" if demo else "Board rebooted · verified") if fault == "watchdog" else "Fault verified · stream healthy"
            detail, tone = evidence("detail"), "good"
        else:
            title, detail, tone = "Test unconfirmed", evidence("detail"), "bad"
        if presenting and phase != "failed":
            if self.presentation_stage == 0:
                title = ("Capturing the simulated event" if demo else "Capturing the live board event") + "." * (self.presentation_tick % 4)
                detail = ("The simulated packet and timing evidence appear as they occur." if demo else
                          "Markers and packets appear as COM3 reports them. The board runs at normal speed.")
            elif self.selected_scenario == "mutex":
                title = "UART timing measured"
                detail = "The result is frozen for viewing. Telemetry continues normally."
            else:
                title = "Reviewing captured evidence"
                detail = "Slow-motion display of captured timestamps; telemetry is not delayed."
            tone = "warn"
        self.state_label.setText(title)
        self.state_detail.setText(detail)
        self.banner.setProperty("tone", tone)
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        step = self.presentation_stage if presenting else (2 if phase == "recovered" else 1 if phase not in ("idle", "failed") else 0)
        for index, label in enumerate(self.step_labels):
            label.setProperty("active", index == step)
            label.style().unpolish(label)
            label.style().polish(label)
        now = time.monotonic()
        age = now - dashboard.last_sample_at if dashboard.last_sample_at is not None else None
        if self.selected_scenario == "watchdog":
            watchdog_phase = visual_phase if fault == "watchdog" else "idle"
            reset_reason = ("IWDG" if dashboard.demo_reset_reason else None) if demo else (
                dashboard.firmware_health.reset_reason if dashboard.firmware_health else None)
            self.watchdog_visual.set_evidence(watchdog_phase, live or (demo and phase != "rebooting"), reset_reason, age,
                                               now - evidence("started_at") if fault == "watchdog" else now)
            self.watchdog_gap.set_evidence(
                evidence("frame_times") if fault == "watchdog" else (),
                evidence("started_at") if fault == "watchdog" else None,
                evidence("gap_start") if fault == "watchdog" else None,
                evidence("stale_at") if fault == "watchdog" else None,
                evidence("reboot_at") if fault == "watchdog" else None,
                evidence("first_return_at") if fault == "watchdog" else None,
                evidence("recovered_at") if fault == "watchdog" else None, now)
        self.health_heading.setText("DEMO HEALTH" if demo else "LIVE HEALTH")
        self.health_values["link"].setText("Demo" if demo_connected else "Offline" if demo else "Live" if live else "Reconnecting")
        self.health_values["sensor"].setText("Streaming" if (live or demo_connected) and age is not None and age < 1.5 else "No new data")
        self.health_values["age"].setText(f"{age:.1f} s" if age is not None and age >= 1 else
                                          f"{age * 1000:.0f} ms" if age is not None else "—")
        self.health_values["rejected"].setText(str(getattr(dashboard.worker, "invalid_frames", 0)))
        reset_reason = (dashboard.demo_reset_reason or "—") if demo else (
            dashboard.firmware_health.reset_reason if dashboard.firmware_health else "—")
        self.health_values["reset"].setText(reset_reason)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "content_layout"):
            return
        narrow = self.width() < 760
        self.content_layout.setDirection(QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight)
        self.rail.setMaximumWidth(16777215 if narrow else 238)
        # The scenario visual needs room for readable bytes and timeline labels.
        # The left control rail still occupies width until the 760px breakpoint.
        self.health_layout.setDirection(QBoxLayout.Direction.TopToBottom if self.width() < 760 else QBoxLayout.Direction.LeftToRight)
        self.event_head.setDirection(QBoxLayout.Direction.TopToBottom if self.width() < 760 else QBoxLayout.Direction.LeftToRight)
        self.proof_head.setDirection(QBoxLayout.Direction.TopToBottom if self.width() < 620 else QBoxLayout.Direction.LeftToRight)
        self.steps_layout.setDirection(QBoxLayout.Direction.TopToBottom if self.width() < 620 else QBoxLayout.Direction.LeftToRight)
        self.health_layout.invalidate()
        self.content_layout.invalidate()
        self.scroll.widget().updateGeometry()

    def closeEvent(self, event):
        self.presentation_timer.stop()
        self.presentation_kind = None
        super().closeEvent(event)
