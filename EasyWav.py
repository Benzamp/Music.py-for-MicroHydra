from lib import st7789fbuf, mhconfig, mhoverlay, smartkeyboard, beeper
from font import vga2_16x32 as font
import os, machine, time, math
from machine import SDCard, Pin
machine.freq(240000000)

"""
EasyWav 
Version: 1

Description:
Gets wav files from a directory on the sd card called 'music'. It then lists this files to be selected and played.

Arrow keys to navigate/change songs, enter to play/pause.
"""

# Constants
_DISPLAY_HEIGHT = const(135)
_DISPLAY_WIDTH = const(240)
_CHAR_HEIGHT = const(32)
_ITEMS_PER_SCREEN = const(_DISPLAY_HEIGHT // _CHAR_HEIGHT)
_CHARS_PER_SCREEN = const(_DISPLAY_WIDTH // 16)
_SCROLL_TIME = const(5000)  # ms per one text scroll
_SCROLLBAR_WIDTH = const(3)
_SCROLLBAR_START_X = const(_DISPLAY_WIDTH - _SCROLLBAR_WIDTH)

# Define pin constants
_SCK_PIN = const(41)
_WS_PIN = const(43)
_SD_PIN = const(42)

# Initialize hardware
tft = st7789fbuf.ST7789(
    machine.SPI(
        1,baudrate=40000000,sck=machine.Pin(36),mosi=machine.Pin(35),miso=None),
    _DISPLAY_HEIGHT,
    _DISPLAY_WIDTH,
    reset=machine.Pin(33, machine.Pin.OUT),
    cs=machine.Pin(37, machine.Pin.OUT),
    dc=machine.Pin(34, machine.Pin.OUT),
    backlight=machine.Pin(38, machine.Pin.OUT),
    rotation=1,
    color_order=st7789fbuf.BGR
)

config = mhconfig.Config()
kb = smartkeyboard.KeyBoard(config=config)
overlay = mhoverlay.UI_Overlay(config, kb, display_fbuf=tft)
beep = beeper.Beeper()

sd = None
i2s = None

def mount_sd():
    global sd
    try:
        if sd is None:
            sd = SDCard(slot=2, sck=Pin(40), miso=Pin(39), mosi=Pin(14), cs=Pin(12))
        os.mount(sd, '/sd')
        print("SD card mounted successfully")
    except OSError as e:
        print("Could not mount SDCard:", str(e))
        overlay.error("SD Card Mount Error")

def read_wav_header(file):
    file.seek(0)
    riff = file.read(12)
    fmt = file.read(24)
    data_hdr = file.read(8)
    
    sample_rate = int.from_bytes(fmt[12:16], 'little')
    return sample_rate * 2

def setup_i2s(sample_rate):
    global i2s
    i2s = machine.I2S(0,
                      sck=machine.Pin(_SCK_PIN),
                      ws=machine.Pin(_WS_PIN),
                      sd=machine.Pin(_SD_PIN),
                      mode=machine.I2S.TX,
                      bits=16,
                      format=machine.I2S.MONO,
                      rate=sample_rate,
                      ibuf=1024)

def ease_in_out_sine(x):
    return -(math.cos(math.pi * x) - 1) / 2

def ping_pong_ease(value, maximum):
    odd_pong = ((value // maximum) % 2 == 1)
    fac = ease_in_out_sine((value % maximum) / maximum)
    return 1 - fac if odd_pong else fac

class WavListView:
    def __init__(self, tft, config):
        self.tft = tft
        self.config = config
        self.items = []
        self.view_index = 0
        self.cursor_index = 0
    
    def load_wav_files(self):
        try:
            self.items = [f for f in os.listdir("/sd/music") if f.lower().endswith('.wav')]
            print("WAV files found:", self.items)
        except OSError as e:
            print("Error loading WAV files:", str(e))
            self.items = []
    
    def draw(self):
        self.tft.fill(self.config["bg_color"])
        if not self.items:
            self.tft.bitmap_text(font, "No WAV files found", 10, 10, self.config.palette[4])
        else:
            for idx in range(0, _ITEMS_PER_SCREEN):
                item_index = idx + self.view_index
                if item_index < len(self.items):
                    color = self.config.palette[5] if item_index == self.cursor_index else self.config.palette[4]
                    text = self.items[item_index]
                    x = 10  # Starting x position
                    
                    # Scroll text if it's too long and it's the selected item
                    if len(text) > _CHARS_PER_SCREEN and item_index == self.cursor_index:
                        scroll_distance = (len(text) - _CHARS_PER_SCREEN) * -16  # Assume 16 pixels per character
                        x += int(ping_pong_ease(time.ticks_ms(), _SCROLL_TIME) * scroll_distance)
                    
                    self.tft.bitmap_text(font, text, x, idx * _CHAR_HEIGHT, color)
            
            # Draw scrollbar
            if len(self.items) > _ITEMS_PER_SCREEN:
                scrollbar_height = _DISPLAY_HEIGHT // max(1, (len(self.items) - _ITEMS_PER_SCREEN + 1))
                scrollbar_y = int((_DISPLAY_HEIGHT - scrollbar_height) * (self.view_index / max(len(self.items) - _ITEMS_PER_SCREEN, 1)))
                self.tft.rect(_SCROLLBAR_START_X, scrollbar_y, _SCROLLBAR_WIDTH, scrollbar_height, self.config.palette[2], fill=True)
        
        self.tft.show()
    
    def up(self):
        if self.items:
            self.cursor_index = (self.cursor_index - 1) % len(self.items)
            self.view_to_cursor()
    
    def down(self):
        if self.items:
            self.cursor_index = (self.cursor_index + 1) % len(self.items)
            self.view_to_cursor()
    
    def view_to_cursor(self):
        if self.cursor_index < self.view_index:
            self.view_index = self.cursor_index
        if self.cursor_index >= self.view_index + _ITEMS_PER_SCREEN:
            self.view_index = self.cursor_index - _ITEMS_PER_SCREEN + 1

def play_sound(notes, time_ms=30):
    if config['ui_sound']:
        beep.play(notes, time_ms, config['volume'])

def main_loop():
    mount_sd()
    view = WavListView(tft, config)
    view.load_wav_files()
    
    while True:
        view.draw()
        
        new_keys = kb.get_new_keys()
        for key in new_keys:
            if key == ";":
                view.up()
                play_sound(("G3","B3"), 30)
            elif key == ".":
                view.down()
                play_sound(("D3","B3"), 30)
            elif key == "ENT" or key == "SPC":
                if view.items:
                    selected_file = view.items[view.cursor_index]
                    try:
                        with open(f"/sd/music/{selected_file}", 'rb') as file:
                            sample_rate = read_wav_header(file)
                            setup_i2s(sample_rate)
                            
                            parts = selected_file.rsplit('.', 1)[0].split(' - ')
                            if len(parts) == 3:
                                artist, album, song = parts
                                overlay.draw_textbox(f"Artist: {artist}", _DISPLAY_WIDTH//2, _DISPLAY_HEIGHT//4)
                                overlay.draw_textbox(f"Album: {album}", _DISPLAY_WIDTH//2, _DISPLAY_HEIGHT//2)
                                overlay.draw_textbox(f"Song: {song}", _DISPLAY_WIDTH//2, 3*_DISPLAY_HEIGHT//4)
                            else:
                                overlay.draw_textbox(f"Playing: {selected_file}", _DISPLAY_WIDTH//2, _DISPLAY_HEIGHT//2)
                                                    
                            tft.show()
                            play_sound(("G3","B3","D3"), 30)
                            
                            while True:
                                data = file.read(1024)
                                if not data:
                                    break
                                i2s.write(data)
                                
                                if kb.get_new_keys():  # Check for key press to stop playback
                                    break
                            
                            i2s.deinit()
                    except Exception as e:
                        print(f"Error playing file: {str(e)}")
                        overlay.error(f"Playback Error: {str(e)[:20]}")
            elif key == "GO":
                return  # Exit the app
        
        time.sleep_ms(10)

try:
    main_loop()
except Exception as e:
    print("Error:", str(e))
    overlay.error(str(e))
finally:
    if sd:
        os.umount('/sd')
        print("SD card unmounted")
