"""Меню NowPlaying в строке меню macOS — своё, на AppKit (PyObjC), по макету «Меню NowPlaying в строке меню».

Сверху вниз: шапка «сейчас играет» (обложка, трек, время, полоска, кнопки назад/пауза/дальше —
меню при нажатии не закрывается), «Экраны» (планшеты и ТВ с точкой «на связи»), «Подключить ещё
экран…» (копирует адрес), «Вид экрана ▸», «Подсветка», «Настройки… ⌘,», «Запускать при входе»,
«Журнал», версия, «Выйти из NowPlaying ⌘Q».

Всё, что касается AppKit, — только в главном потоке. Меню пересобирается при каждом открытии
(menuNeedsUpdate:), пока оно открыто, шапку раз в секунду обновляет таймер.
"""
import logging
import os
import threading
import time
import urllib.request

import AppKit
import Foundation
import objc

import server

log = logging.getLogger('nowplaying')
ONLINE_SEC = 120          # устройство «на связи», если заходило не позже стольких секунд назад
THEMES = (('classic', 'Классика'), ('scene', 'Сцена'), ('poster', 'Постер'), ('minimal', 'Минимал'), ('vinyl', 'Винил'))
HEADER_W, HEADER_H = 300, 112
_keep = []
MENU_W = 300              # ширина, под которую выравниваем серые подписи справа


def _plural(n, one, few, many):
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _mmss(ms):
    s = max(0, int(ms // 1000))
    return f'{s // 60}:{s % 60:02d}'


def _symbol(name, size=15, weight=None):
    img = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if img is None:
        return None
    cfg = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
        size, weight if weight is not None else AppKit.NSFontWeightRegular)
    return img.imageWithSymbolConfiguration_(cfg) or img


def _dot(color):
    img = AppKit.NSImage.alloc().initWithSize_((8, 8))
    img.lockFocus()
    color.set()
    AppKit.NSBezierPath.bezierPathWithOvalInRect_(((0, 0), (8, 8))).fill()
    img.unlockFocus()
    return img


def _detail_title(title, detail):
    """Заголовок пункта и серая подпись, прижатая к правому краю меню (через табуляцию)."""
    font = AppKit.NSFont.menuFontOfSize_(0)
    para = AppKit.NSMutableParagraphStyle.alloc().init()
    para.setTabStops_([AppKit.NSTextTab.alloc().initWithTextAlignment_location_options_(
        AppKit.NSTextAlignmentRight, MENU_W - 40, {})])
    s = AppKit.NSMutableAttributedString.alloc().initWithString_attributes_(
        title, {AppKit.NSFontAttributeName: font, AppKit.NSParagraphStyleAttributeName: para})
    if detail:
        s.appendAttributedString_(AppKit.NSAttributedString.alloc().initWithString_attributes_(
            '\t' + detail, {AppKit.NSFontAttributeName: AppKit.NSFont.menuFontOfSize_(12),
                            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.secondaryLabelColor(),
                            AppKit.NSParagraphStyleAttributeName: para}))
    return s


def _label(text, size, weight, color):
    f = AppKit.NSTextField.labelWithString_(text)
    f.setFont_(AppKit.NSFont.systemFontOfSize_weight_(size, weight))
    f.setTextColor_(color)
    f.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
    return f


def _bar(frame, color):
    v = AppKit.NSView.alloc().initWithFrame_(frame)
    v.setWantsLayer_(True)
    v.layer().setCornerRadius_(1.5)
    v.layer().setBackgroundColor_(color.CGColor())
    return v


class MenuController(AppKit.NSObject):
    """Делегат меню и цель всех пунктов."""

    def initWithServer_(self, ctx):
        self = objc.super(MenuController, self).init()
        if self is None:
            return None
        self.ctx = ctx               # dict: srv, open_settings, open_path, log_path, autostart, set_autostart, quit, local_ip
        self.header = None
        self.timer = None
        self.last_art = None
        return self

    # ---------- построение меню ----------

    def menuNeedsUpdate_(self, menu):
        try:
            self.build(menu)
        except Exception:
            log.exception('menu build')

    @objc.python_method
    def build(self, menu):
        menu.removeAllItems()
        st = server.state
        cfg = server.SETTINGS.get()

        # шапка «сейчас играет»
        if st.get('track_id'):
            it = AppKit.NSMenuItem.alloc().init()
            it.setView_(self.make_header())
            menu.addItem_(it)
        else:
            self.header = None
            menu.addItem_(self.item('Ничего не играет', None, enabled=False))
        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        # экраны
        if hasattr(AppKit.NSMenuItem, 'sectionHeaderWithTitle_'):
            menu.addItem_(AppKit.NSMenuItem.sectionHeaderWithTitle_('Экраны'))
        else:
            menu.addItem_(self.item('Экраны', None, enabled=False))
        green, grey = _dot(AppKit.NSColor.systemGreenColor()), _dot(AppKit.NSColor.systemGrayColor())
        for d in server.SETTINGS.device_list():
            on = d.get('seen') is not None and d['seen'] < ONLINE_SEC
            it = self.item('', 'openSettings:')
            it.setAttributedTitle_(_detail_title(d['name'], 'на связи' if on else 'нет связи'))
            it.setImage_(green if on else grey)
            menu.addItem_(it)
        addr = f"{self.ctx['local_ip']()}:{server.HTTP_PORT}"
        it = self.item('', 'copyAddress:')
        it.setAttributedTitle_(_detail_title('Подключить ещё экран…', addr))
        it.setRepresentedObject_(addr)
        it.setToolTip_('Скопировать адрес: введите его на планшете или ТВ, если он не нашёл компьютер сам')
        menu.addItem_(it)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        # вид экрана
        theme = cfg.get('theme', 'classic')
        names = dict(THEMES)
        it = self.item('', None)
        it.setAttributedTitle_(_detail_title('Вид экрана', names.get(theme, '')))
        sub = AppKit.NSMenu.alloc().initWithTitle_('Вид экрана')
        for key, name in THEMES:
            t = self.item(name, 'setTheme:')
            t.setRepresentedObject_(key)
            t.setState_(AppKit.NSControlStateValueOn if key == theme else AppKit.NSControlStateValueOff)
            sub.addItem_(t)
        it.setSubmenu_(sub)
        menu.addItem_(it)

        # подсветка
        parts = []
        if cfg.get('strip_id') or cfg.get('strip_addr'):
            parts.append('лента')
        n = len(cfg.get('lamp_ids') or [])
        if n:
            parts.append(f"{n} {_plural(n, 'лампа', 'лампы', 'ламп')}")
        it = self.item('', 'toggleLight:')
        it.setAttributedTitle_(_detail_title('Подсветка', ' и '.join(parts) if parts else 'не настроена'))
        it.setState_(AppKit.NSControlStateValueOn if (cfg.get('lamp_on') or cfg.get('strip_on'))
                     else AppKit.NSControlStateValueOff)
        menu.addItem_(it)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        # служебное
        menu.addItem_(self.item('Настройки…', 'openSettings:', key=','))
        if self.ctx['can_autostart']:
            it = self.item('Запускать при входе', 'toggleAutostart:')
            it.setState_(AppKit.NSControlStateValueOn if self.ctx['autostart']() else AppKit.NSControlStateValueOff)
            menu.addItem_(it)
        menu.addItem_(self.item('Журнал', 'openLog:'))
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        menu.addItem_(self.item(f'NowPlaying {server.VERSION}', None, enabled=False))
        menu.addItem_(self.item('Выйти из NowPlaying', 'quit:', key='q'))

    @objc.python_method
    def item(self, title, action, key='', enabled=True):
        it = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
        if action:
            it.setTarget_(self)
        it.setEnabled_(enabled and action is not None)
        return it

    # ---------- шапка ----------

    @objc.python_method
    def make_header(self):
        v = AppKit.NSView.alloc().initWithFrame_(((0, 0), (HEADER_W, HEADER_H)))
        art = AppKit.NSImageView.alloc().initWithFrame_(((14, HEADER_H - 10 - 48), (48, 48)))
        art.setImageScaling_(AppKit.NSImageScaleProportionallyUpOrDown)
        art.setWantsLayer_(True)
        art.layer().setCornerRadius_(6)
        art.layer().setMasksToBounds_(True)
        v.addSubview_(art)
        title = _label('', 13, AppKit.NSFontWeightSemibold, AppKit.NSColor.labelColor())
        title.setFrame_(((72, HEADER_H - 10 - 20), (HEADER_W - 72 - 14, 18)))
        v.addSubview_(title)
        sub = _label('', 12, AppKit.NSFontWeightRegular, AppKit.NSColor.secondaryLabelColor())
        sub.setFrame_(((72, HEADER_H - 10 - 40), (HEADER_W - 72 - 14, 16)))
        v.addSubview_(sub)
        track = _bar(((14, 44), (HEADER_W - 28, 3)), AppKit.NSColor.labelColor().colorWithAlphaComponent_(0.12))
        fill = _bar(((14, 44), (0, 3)), AppKit.NSColor.labelColor().colorWithAlphaComponent_(0.65))
        v.addSubview_(track)
        v.addSubview_(fill)
        buttons = {}
        cx = HEADER_W / 2
        for i, (name, sym, action) in enumerate((('prev', 'backward.fill', 'prevTrack:'),
                                                 ('toggle', 'pause.fill', 'togglePlay:'),
                                                 ('next', 'forward.fill', 'nextTrack:'))):
            b = AppKit.NSButton.buttonWithImage_target_action_(_symbol(sym, 17), self, action)
            b.setBordered_(False)
            b.setFrame_(((cx - 15 + (i - 1) * 52, 10), (30, 26)))
            b.setToolTip_({'prev': 'Предыдущий трек', 'toggle': 'Пауза', 'next': 'Следующий трек'}[name])
            v.addSubview_(b)
            buttons[name] = b
        self.header = {'view': v, 'art': art, 'title': title, 'sub': sub, 'fill': fill, 'buttons': buttons}
        self.last_art = None
        self.refresh_header()
        return v

    @objc.python_method
    def refresh_header(self):
        h = self.header
        if not h:
            return
        st = server.state
        tid = st.get('track_id') or ''
        h['title'].setStringValue_(st.get('title') or '')
        dur = st.get('duration_ms') or 0
        pos = st.get('position_ms') or 0
        if st.get('playing') and st.get('measured_at'):
            pos += server.now_ms() - st['measured_at']
        pos = min(pos, dur) if dur else pos
        sub = st.get('artist') or ''
        if dur:
            sub = (sub + ' · ' if sub else '') + f'{_mmss(pos)} из {_mmss(dur)}'
        h['sub'].setStringValue_(sub)
        w = (HEADER_W - 28) * (pos / dur if dur else 0)
        h['fill'].setFrameSize_((max(0, w), 3))
        playing = bool(st.get('playing'))
        h['buttons']['toggle'].setImage_(_symbol('pause.fill' if playing else 'play.fill', 17))
        h['buttons']['toggle'].setToolTip_('Пауза' if playing else 'Играть')
        if tid != self.last_art:
            a = server.art.get(tid)
            if a:
                data = Foundation.NSData.dataWithBytes_length_(a[0], len(a[0]))
                h['art'].setImage_(AppKit.NSImage.alloc().initWithData_(data))
                self.last_art = tid

    # ---------- открытое меню: шапка живёт ----------

    def menuWillOpen_(self, menu):
        self.timer = Foundation.NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, self, 'tick:', None, True)
        Foundation.NSRunLoop.currentRunLoop().addTimer_forMode_(self.timer, Foundation.NSRunLoopCommonModes)

    def menuDidClose_(self, menu):
        if self.timer:
            self.timer.invalidate()
            self.timer = None

    def tick_(self, timer):
        try:
            self.refresh_header()
        except Exception:
            log.exception('menu header refresh')

    # ---------- действия ----------

    @objc.python_method
    def control(self, action):
        def go():
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{server.HTTP_PORT}/api/control/{action}', timeout=4).read()
            except Exception as e:
                log.warning('menu control %s: %s', action, e)
        threading.Thread(target=go, daemon=True).start()

    def prevTrack_(self, sender):
        self.control('prev')

    def togglePlay_(self, sender):
        self.control('toggle')

    def nextTrack_(self, sender):
        self.control('next')

    @objc.python_method
    def apply(self, changes):
        """Настройки меняем в потоке сервера: слушатели настроек живут там."""
        srv = self.ctx['srv']
        if srv.loop:
            srv.loop.call_soon_threadsafe(server.SETTINGS.update, changes)

    def setTheme_(self, sender):
        self.apply({'theme': str(sender.representedObject())})

    def toggleLight_(self, sender):
        cfg = server.SETTINGS.get()
        if cfg.get('lamp_on') or cfg.get('strip_on'):
            self.apply({'lamp_on': False, 'strip_on': False})
            return
        changes = {}
        if cfg.get('lamp_ids'):
            changes['lamp_on'] = True
        if cfg.get('strip_id') or cfg.get('strip_addr'):
            changes['strip_on'] = True
        if changes:
            self.apply(changes)
        else:
            self.ctx['open_settings']()      # подсветка ещё не настроена — в настройки

    def copyAddress_(self, sender):
        pb = AppKit.NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(str(sender.representedObject()), AppKit.NSPasteboardTypeString)

    def openSettings_(self, sender):
        self.ctx['open_settings']()

    def toggleAutostart_(self, sender):
        self.ctx['set_autostart'](not self.ctx['autostart']())

    def openLog_(self, sender):
        if self.ctx['log_path']:
            self.ctx['open_path'](self.ctx['log_path'])

    def quit_(self, sender):
        self.ctx['srv'].stop()
        AppKit.NSApp.terminate_(None)

    def watch_(self, timer):
        if not self.ctx['srv'].is_alive():
            log.warning('server thread stopped, quitting')
            AppKit.NSApp.terminate_(None)


def run(ctx, glyph_path, glyph_size):
    """Значок и меню в строке меню; блокирует главный поток до выхода."""
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(AppKit.NSVariableStatusItemLength)
    if os.path.exists(glyph_path):
        with open(glyph_path, 'rb') as f:
            raw = f.read()
        img = AppKit.NSImage.alloc().initWithData_(Foundation.NSData.dataWithBytes_length_(raw, len(raw)))
        img.setSize_(glyph_size)
        img.setTemplate_(True)
        item.button().setImage_(img)
    else:
        item.button().setTitle_('NP')
    item.button().setToolTip_('NowPlaying Server')
    ctl = MenuController.alloc().initWithServer_(ctx)
    menu = AppKit.NSMenu.alloc().initWithTitle_('NowPlaying')
    menu.setDelegate_(ctl)
    menu.setAutoenablesItems_(False)
    item.setMenu_(menu)
    watch = Foundation.NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(3.0, ctl, 'watch:', None, True)
    Foundation.NSRunLoop.currentRunLoop().addTimer_forMode_(watch, Foundation.NSRunLoopCommonModes)
    if os.environ.get('NP_MENU_SELFTEST'):
        # проверка на сборочном сервере: меню собирается без ошибок
        ctl.build(menu)
        log.info('menu selftest: %d items', menu.numberOfItems())
    log.info('tray icon shown')
    # держим ссылки, иначе сборщик мусора уберёт значок
    _keep[:] = [item, ctl, menu, watch]
    app.run()
