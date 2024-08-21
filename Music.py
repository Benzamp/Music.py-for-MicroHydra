from lib import st7789fbuf, mhconfig, mhoverlay, smartkeyboard, beeper
from font import vga2_16x32 as font
from font import vga1_8x16 as small_font  # Import a smaller font
import os, machine, time, math, framebuf, random, urequests
from machine import SDCard, Pin
from micropython import const
machine.freq(240000000)

"""
Music App
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

# New constants for the smaller font
_SMALL_CHAR_HEIGHT = const(16)
_SMALL_CHAR_WIDTH = const(8)
_SMALL_CHARS_PER_SCREEN = const(_DISPLAY_WIDTH // _SMALL_CHAR_WIDTH)

# New constants for optimization
_UPDATE_INTERVAL = const(1000)  # Update display every 1 second
_PROGRESS_BAR_Y = const(100)  # Fixed Y position for progress bar
_PROGRESS_BAR_HEIGHT = const(10)
_PROGRESS_BAR_WIDTH = const(_DISPLAY_WIDTH - 20)

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
        
def display_play_screen(selected_file, duration, current_position):
    # Clear the screen
    tft.fill(config["bg_color"])
    
    # Load and display the background image
    #load_and_display_image(selected_file) TODO - Get cover art background on play if possible.
    
    # Display song info
    parts = selected_file.rsplit('.', 1)[0].split(' - ')
    
    if len(parts) == 3:
        artist, album, song = parts
        info = [
            f"Artist: {artist}",
            f"Album: {album}",
            f"Song: {song}"
        ]
    else:
        info = [f"Playing: {selected_file}"]
    
    # Calculate starting y position to center the text vertically
    total_height = len(info) * _SMALL_CHAR_HEIGHT + 20  # Add extra space for progress bar
    start_y = (_DISPLAY_HEIGHT - total_height) // 2
    
    for idx, text in enumerate(info):
        y = start_y + idx * _SMALL_CHAR_HEIGHT
        x = 10  # Left align with a small margin
        
        # Truncate text if it's too long
        if len(text) > _SMALL_CHARS_PER_SCREEN:
            text = text[:_SMALL_CHARS_PER_SCREEN - 3] + "..."
        
        tft.bitmap_text(small_font, text, x, y, config.palette[4])
    
    # Draw progress bar
    bar_y = start_y + len(info) * _SMALL_CHAR_HEIGHT + 10
    bar_height = 10
    bar_width = _DISPLAY_WIDTH - 20  # Full width minus margins
    
    # Draw background of progress bar
    tft.fill_rect(10, bar_y, bar_width, bar_height, config.palette[2])
    
    # Draw filled portion of progress bar
    if duration > 0:
        fill_width = int((current_position / duration) * bar_width)
        tft.fill_rect(10, bar_y, fill_width, bar_height, config.palette[5])
    
    # Display time
    current_time = format_time(current_position)
    total_time = format_time(duration)
    time_text = f"{current_time} / {total_time}"
    time_x = (_DISPLAY_WIDTH - len(time_text) * _SMALL_CHAR_WIDTH) // 2
    time_y = bar_y + bar_height + 5
    tft.bitmap_text(small_font, time_text, time_x, time_y, config.palette[4])
    
    tft.show()

def format_time(seconds):
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes:02d}:{secs:02d}"

class EasyWavMenu:       
    def __init__(self, tft, config):
        self.tft = tft
        self.config = config
        self.main_items = ['Library', 'Shuffle', 'Settings']
        self.library_items = ['Artists', 'Albums', 'Songs']
        self.cursor_index = 0
        self.view_index = 0
        self.current_view = 'main'
        self.items = self.main_items
        self.artists = []
        self.albums = []
        self.songs = []
        self.songs_by_artist = {}
        self.songs_by_album = {}
        self.current_artist = None
        self.current_album = None
        self.populate_music_lists()
    
    def populate_music_lists(self):
        music_dir = '/sd/music'  # Adjust this path as needed
        self.artists = []
        self.albums = []
        self.songs = []
        self.songs_by_artist = {}
        self.songs_by_album = {}

        try:
            for filename in os.listdir(music_dir):
                if filename.endswith('.wav'):
                    parts = filename[:-4].split(' - ')
                    if len(parts) == 3:
                        artist, album, song = parts
                        
                        if artist not in self.artists:
                            self.artists.append(artist)
                            self.songs_by_artist[artist] = []
                        
                        if album not in self.albums:
                            self.albums.append(album)
                            self.songs_by_album[album] = []
                        
                        if song not in self.songs:
                            self.songs.append(song)
                        
                        self.songs_by_artist[artist].append(song)
                        self.songs_by_album[album].append(song)

            # Sort the lists
            self.artists.sort()
            self.albums.sort()
            self.songs.sort()
            for artist in self.songs_by_artist:
                self.songs_by_artist[artist].sort()
            for album in self.songs_by_album:
                self.songs_by_album[album].sort()

        except OSError as e:
            print(f"Error accessing music directory: {e}")

    def draw(self):
        self.tft.fill(self.config["bg_color"])
        if self.current_view == 'main':
            self._draw_items(self.main_items)
        elif self.current_view == 'library_submenu':
            self._draw_items(self.library_items)
        elif self.current_view == 'artists':
            self._draw_items(self.artists)
        elif self.current_view == 'albums':
            self._draw_items(self.albums)
        elif self.current_view == 'songs':
            self._draw_items(self.songs)
        elif self.current_view == 'artist_songs':
            self._draw_items(self.songs_by_artist[self.current_artist])
        elif self.current_view == 'album_songs':
            self._draw_items(self.songs_by_album[self.current_album])
        self.tft.show()

    def _draw_items(self, items):
        for idx, item in enumerate(items[self.view_index:self.view_index + _ITEMS_PER_SCREEN]):
            color = self.config.palette[5] if idx + self.view_index == self.cursor_index else self.config.palette[4]
            self.tft.bitmap_text(font, item, 10, idx * _CHAR_HEIGHT, color)

    def get_full_filename(self, song):
            for artist in self.songs_by_artist:
                if song in self.songs_by_artist[artist]:
                    for album in self.songs_by_album:
                        if song in self.songs_by_album[album]:
                            return f"{artist} - {album} - {song}.wav"
            return None

    def select(self):
        if self.current_view == 'main':
            selected_item = self.main_items[self.cursor_index]
            if selected_item == 'Library':
                self.current_view = 'library_submenu'
                self.cursor_index = 0
                self.view_index = 0
                self.items = self.library_items
            elif selected_item == 'Shuffle':
                return self.shuffle_play()
            elif selected_item == 'Settings':
                return self.show_coming_soon_message()
        elif self.current_view == 'library_submenu':
            selected_item = self.library_items[self.cursor_index]
            if selected_item == 'Artists':
                self.current_view = 'artists'
                self.cursor_index = 0
                self.view_index = 0
                self.items = self.artists
            elif selected_item == 'Songs':
                self.current_view = 'songs'
                self.cursor_index = 0
                self.view_index = 0
                self.items = self.songs
            elif selected_item == 'Albums':
                self.current_view = 'albums'
                self.cursor_index = 0
                self.view_index = 0
                self.items = self.albums
            else:
                return self.show_coming_soon_message(f"{selected_item} view")
        elif self.current_view == 'artists':
            self.current_artist = self.artists[self.cursor_index]
            self.current_view = 'artist_songs'
            self.cursor_index = 0
            self.view_index = 0
            self.items = self.songs_by_artist[self.current_artist]
        elif self.current_view == 'albums':
            self.current_album = self.albums[self.cursor_index]
            self.current_view = 'album_songs'
            self.cursor_index = 0
            self.view_index = 0
            self.items = self.songs_by_album[self.current_album]
        elif self.current_view in ['songs', 'artist_songs', 'album_songs']:
            selected_song = self.items[self.cursor_index]
            full_filename = self.get_full_filename(selected_song)
            if full_filename:
                return "play", full_filename
        return "refresh"

    def shuffle_play(self):
        if self.songs:
            random_song = random.choice(self.songs)
            full_filename = self.get_full_filename(random_song)
            if full_filename:
                return "play_shuffle", full_filename
        print("No songs available for shuffle play")
        return None

    def show_coming_soon_message(self, feature="Settings"):
        overlay.draw_textbox(f"{feature}", _DISPLAY_WIDTH//2, _DISPLAY_HEIGHT//2 - 10)
        overlay.draw_textbox("in progress", _DISPLAY_WIDTH//2, _DISPLAY_HEIGHT//2 + 10)
        self.tft.show()
        time.sleep(2)
        return "refresh"

    def up(self):
        if self.cursor_index > 0:
            self.cursor_index -= 1
            if self.cursor_index < self.view_index:
                self.view_index = self.cursor_index
        elif self.cursor_index == 0 and self.view_index > 0:
            self.view_index -= 1
            self.cursor_index = self.view_index

    def down(self):
        if self.cursor_index < len(self.items) - 1:
            self.cursor_index += 1
            if self.cursor_index >= self.view_index + _ITEMS_PER_SCREEN:
                self.view_index = self.cursor_index - _ITEMS_PER_SCREEN + 1

    def back(self):
        if self.current_view == 'library_submenu':
            self.current_view = 'main'
            self.items = self.main_items
        elif self.current_view in ['artists', 'albums', 'songs']:
            self.current_view = 'library_submenu'
            self.items = self.library_items
        elif self.current_view == 'artist_songs':
            self.current_view = 'artists'
            self.items = self.artists
            self.current_artist = None
        elif self.current_view == 'album_songs':
            self.current_view = 'albums'
            self.items = self.albums
            self.current_album = None
        else:
            return False
        self.cursor_index = 0
        self.view_index = 0
        return True

    def handle_input(self, key):
        if key == ";":
            self.up()
            return "up"
        elif key == ".":
            self.down()
            return "down"
        elif key in ("ENT", "SPC"):
            return self.select()
        elif key in ("`", "DEL", "ESC", "BKSP"):
            if self.back():
                return "back"
            else:
                return "exit"
        return None

def play_sound(notes, time_ms=30):
    if config['ui_sound']:
        beep.play(notes, time_ms, config['volume'])

def main_loop():
    mount_sd()
    view = EasyWavMenu(tft, config)
    
    while True:
        view.draw()
        
        new_keys = kb.get_new_keys()
        for key in new_keys:
            action = view.handle_input(key)
            
            if action == "up":
                play_sound(("G3","B3"), 30)
            elif action == "down":
                play_sound(("D3","B3"), 30)
            elif action == "select":
                play_sound(("G3","B3","D3"), 30)
                
            if isinstance(action, tuple) and action[0] in ["play", "play_shuffle"]:
                selected_file = action[1]
                try:
                    with open(f"/sd/music/{selected_file}", 'rb') as file:
                        sample_rate = read_wav_header(file)
                        setup_i2s(sample_rate)
                        
                        # Get file size for duration calculation
                        file.seek(0, 2)
                        file_size = file.tell()
                        file.seek(44)  # Skip WAV header
                        
                        # Calculate total duration (approximate)
                        duration = (file_size - 44) / (sample_rate * 2)  # 16-bit mono
                        
                        start_time = time.ticks_ms()
                        while True:
                            data = file.read(1024)
                            if not data:
                                break
                            i2s.write(data)
                            
                            # Calculate current position
                            current_position = (time.ticks_ms() - start_time) / 1000
                            
                            # Update display every 1000ms
                            if time.ticks_ms() % 1000 == 0:
                               display_play_screen(selected_file, duration, current_position)
                            
                            if kb.get_new_keys():  # Check for key press to stop playback
                                break
                        
                        i2s.deinit()
                except Exception as e:
                    print(f"Error playing file: {str(e)}")
                    overlay.error(f"Playback Error: {str(e)[:20]}")
                    
            elif action == "back":
                play_sound(("D3","B3","G3"), 30)
            elif action == "exit":
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
