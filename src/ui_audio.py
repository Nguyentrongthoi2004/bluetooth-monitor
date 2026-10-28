"""Âm thanh phát ra: công tắc Loa ↔ Tai nghe trên bảng pin, phím tắt và menu khay."""
from pystray import MenuItem as item

import audio_output
import i18n
import ui_theme as T
from i18n import tr

i18n.register({
    'Không đọc được thiết bị phát': "Can't read the output device",
    '{name} · qua {enhancer}': '{name} · via {enhancer}',
    'Không có thiết bị phát nào khác để chuyển': 'No other output device to switch to',
    'Đang phát qua': 'Now playing through',
    'Không đổi được thiết bị phát': "Couldn't change the output device",
})

# Bộ tăng cường âm thanh dạng thiết bị ảo (không phải loa): bỏ qua khi chọn "Loa".
AUDIO_ENHANCERS = ('FxSound',)


class AudioMixin:
    def _render_audio_row(self):
        """Hiện hàng âm thanh (trừ chế độ gọn) theo thiết bị phát hiện tại của Windows."""
        if not self.settings['show_audio_control'] or self.settings['compact_mode']:
            if self.audio_row.winfo_manager():
                self.audio_row.pack_forget()
                self.audio_holder.configure(height=1)
            return
        outputs = self._audio_outputs = audio_output.list_outputs()
        effective, enhancer = self._effective_output(outputs, audio_output.default_output_id())
        pair = self._audio_pair(outputs, effective)
        name = next((label for device_id, label in outputs if device_id == effective), None)
        text = self._short_output_name(self._output_label(name)) if name else tr("Không đọc được thiết bị phát")
        if enhancer and name:
            text = tr("{name} · qua {enhancer}", name=text, enhancer=self._enhancer_name(enhancer))
        self.lbl_audio_device.configure(text=text)
        # Công tắc hiện chữ đã dịch; _on_audio_switch đổi lại về 'Loa' / 'Tai nghe'.
        labels = [tr(label) for label, _ in pair]
        if list(self.audio_switch.cget('values')) != labels:
            self.audio_switch.configure(values=labels)
        self.audio_switch.set(next((tr(label) for label, output in pair if output and output[0] == effective), ''))
        available = [output for _, output in pair if output]
        self.audio_switch.configure(state="normal" if len(available) == 2 else "disabled")
        if not self.audio_row.winfo_manager():
            self.audio_row.pack(fill="x", pady=(0, 8))

    @staticmethod
    def _output_label(name):
        """Tên thiết bị phát để hiện: chỉ tên thay thế của app (thiết bị không tên) được dịch."""
        return tr(name) if name == audio_output.UNNAMED else name

    @staticmethod
    def _short_output_name(name):
        """'FxSound Speakers (FxSound Audio Enhancer)' -> gọn hơn cho dòng phụ; tai nghe giữ tên trong ngoặc."""
        if name.startswith(('Headphones (', 'Headset (', 'Tai nghe (')) and name.endswith(')'):
            return name[name.index('(') + 1:-1]
        if len(name) > 30 and ' (' in name:
            name = name[:name.index(' (')]
        return name if len(name) <= 34 else name[:33] + '…'

    def _on_audio_switch(self, value):
        value = {tr(label): label for label in ('Loa', 'Tai nghe')}.get(value, value)  # chữ hiện -> giá trị gốc
        outputs = self._audio_outputs or audio_output.list_outputs()
        effective, _ = self._effective_output(outputs, audio_output.default_output_id())
        target = dict(self._audio_pair(outputs, effective)).get(value)
        if target and target[0] != effective:
            self._set_audio_output(self._with_enhancer(outputs, target))
        else:
            self._render_audio_row()  # đang phát ra đó rồi (kể cả qua FxSound): giữ nguyên

    # --- ÂM THANH PHÁT RA ---
    @staticmethod
    def _is_enhancer(output):
        return bool(output) and any(name in output[1] for name in AUDIO_ENHANCERS)

    @staticmethod
    def _enhancer_name(output):
        return next((name for name in AUDIO_ENHANCERS if name in output[1]), output[1])

    def _effective_output(self, outputs, current):
        """(thiết bị thật đang phát, bộ tăng cường hoặc None).

        Mặc định là FxSound thì âm thanh thật ra ở thiết bị FxSound đang dùng (vd. tai nghe JBL), nên công tắc
        phải sáng ở bên đó chứ không trống."""
        output = next((output for output in outputs if output[0] == current), None)
        if self._is_enhancer(output):
            target = audio_output.fxsound_output_id()
            if any(device_id == target for device_id, _ in outputs):
                return target, output
        return current, None

    def _with_enhancer(self, outputs, target):
        """Quay lại thiết bị đã nghe qua FxSound thì quay lại qua FxSound, không mất hiệu ứng tăng cường."""
        previous = next((output for output in outputs if output[0] == self._previous_output), None)
        if self._is_enhancer(previous) and audio_output.fxsound_output_id() == target[0]:
            return previous
        return target
    def _headphone_output(self, outputs):
        """Thiết bị phát của tai nghe đang chọn (Windows đặt tên tai nghe trong ngoặc)."""
        device = self.active_headphone
        if not device:
            return None
        return next((output for output in outputs if f"({device['name']})" in output[1]), None)

    def _audio_pair(self, outputs, current):
        """Hai lựa chọn của công tắc: [('Loa', loa thật), ('Tai nghe', tai nghe đang chọn)].

        Bộ tăng cường (FxSound) là thiết bị ảo, không phải loa, nên không bao giờ là lựa chọn "Loa".
        """
        headphone = self._headphone_output(outputs)
        speakers = [output for output in outputs if output != headphone
                    and not any(name in output[1] for name in AUDIO_ENHANCERS)]
        speaker = (next((output for output in speakers if output[0] == current), None)
                   or next((output for output in speakers if output[0] == self._previous_output), None)
                   or (speakers[0] if speakers else None))
        return [('Loa', speaker), ('Tai nghe', headphone)]

    def _toggle_audio_output(self):
        outputs = audio_output.list_outputs()
        current, _ = self._effective_output(outputs, audio_output.default_output_id())
        (_, left), (_, right) = self._audio_pair(outputs, current)
        target = left if right is None or (right and current == right[0]) else right
        if target is None or target[0] == current:
            self._show_toast(tr('Âm thanh phát ra'), tr('Không có thiết bị phát nào khác để chuyển'),
                             glyph='speaker', color=T.WARN)
            return
        self._set_audio_output(self._with_enhancer(outputs, target))

    def _set_audio_output(self, output):
        current = audio_output.default_output_id()
        if current and current != output[0]:
            self._previous_output = current
        ok = audio_output.set_default_output(output[0])
        self._render_audio_row()
        self._show_toast(tr('Đang phát qua' if ok else 'Không đổi được thiết bị phát'), self._output_label(output[1]),
                         glyph='speaker', color=T.ACCENT if ok else T.WARN, duration=3000)
        self._audio_menu_version += 1
        self._update_tray()

    def _tray_audio_items(self):
        outputs = audio_output.list_outputs()
        current = audio_output.default_output_id()
        if not outputs:
            return [item(tr("Không đọc được thiết bị phát"), None, enabled=False)]

        def choose(output):
            return lambda: self._enqueue_ui(self._set_audio_output, output)
        return [item(self._output_label(name), choose((device_id, name)),
                     checked=lambda menu_item, key=device_id: key == current, radio=True)
                for device_id, name in outputs]


