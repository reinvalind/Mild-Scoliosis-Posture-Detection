import socket
import threading
import time
import csv
import sys
import os
from datetime import datetime

# --- KONFIGURASI JARINGAN ESP32 ---
# Ganti IP ini dengan IP yang Anda tetapkan pada kode thoraric.cpp dan lumbar.cpp
THORACIC_IP = '10.134.179.112' 
THORACIC_PORT = 8001
LUMBAR_IP = '10.134.179.36'
LUMBAR_PORT = 8002

# --- VARIABEL GLOBAL DAN KONEKSI ---
thoracic_socket = None
lumbar_socket = None
is_running = True
is_sampling = False
is_calibrating = False
data_buffer = []

# Buffer data terbaru dari ESP32
# [T_sagital, T_coronal, L_sagital, L_coronal]
current_data = [None, None, None, None] 

# Buffer data referensi (diisi setelah kalibrasi)
thoracic_ref = None
lumbar_ref = None

# Variabel status lama diganti dengan yang lebih detail
thoracic_ready = False
lumbar_ready = False
calibration_status_granular = {
    "Thoracic": {"Atas": "Menunggu...", "Bawah": "Menunggu..."},
    "Lumbar":   {"Atas": "Menunggu...", "Bawah": "Menunggu..."}
}


# --- FUNGSI KONEKSI DAN KOMUNIKASI ---
def connect_esp(ip, port):
    """Mencoba membuat koneksi ke ESP32."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5) # Batas waktu koneksi 5 detik
        print(f"[INFO] Mencoba menghubungkan ke ESP32 di {ip}:{port}...")
        s.connect((ip, port))
        s.settimeout(None) # Hapus batas waktu setelah terhubung
        print(f"[BERHASIL] Terhubung ke {ip}:{port}.")
        return s
    except socket.error as e:
        print(f"[GAGAL] Gagal terhubung ke {ip}:{port}. Pastikan ESP32 berjalan dan IP benar.")
        print(f"Error: {e}")
        return None

def send_command(sock, command):
    """Mengirim perintah karakter tunggal ke ESP32."""
    if sock:
        try:
            sock.sendall(command.encode('ascii'))
        except socket.error:
            print("[ERROR] Gagal mengirim perintah. Koneksi terputus.")

def receive_data(sock, device_name, index_offset):
    """Menerima data secara terus-menerus dari satu ESP32."""
    # --- INI ADALAH PERBAIKANNYA ---
    # Memberi tahu thread ini untuk menggunakan variabel global
    global is_running, is_calibrating, current_data, thoracic_ref, lumbar_ref, thoracic_ready, lumbar_ready, calibration_status_granular
    # --- AKHIR PERBAIKAN ---

    buffer = ""
    while is_running:
        try:
            data = sock.recv(1024).decode('ascii')
            if not data:
                print(f"[WARNING] {device_name} terputus.")
                break

            buffer += data
            lines = buffer.split('\n')
            buffer = lines.pop()

            for line in lines:
                line = line.strip()
                if not line: continue

                # Cek untuk status kalibrasi
                if "STATUS:" in line:
                    # Format baru: "STATUS:NAMA_STATUS:NILAI" atau "STATUS:NAMA_STATUS"
                    parts = line.split(":")
                    status = parts[1].strip()
                    value = None
                    if len(parts) > 2:
                        try:
                            value = float(parts[2])
                        except ValueError:
                            value = None
                    
                    # Kirim ke handle_status
                    handle_status(device_name, status, value)
                
                # Cek untuk data sudut sampling
                elif line.startswith('T:') or line.startswith('L:'):
                    try:
                        _, values = line.split(":")
                        sagital, coronal = map(float, values.split(','))
                        
                        current_data[index_offset] = sagital
                        current_data[index_offset + 1] = coronal
                        
                    except ValueError:
                        pass # Abaikan baris data yang rusak

                # Cek untuk data referensi
                elif line.startswith('REF:'):
                    try:
                        _, values = line.split(":")
                        sagital, coronal = map(float, values.split(','))
                        if device_name == "Thoracic":
                            thoracic_ref = (sagital, coronal)
                        elif device_name == "Lumbar":
                            lumbar_ref = (sagital, coronal)
                    except ValueError:
                        pass

        except socket.timeout:
            continue
        except socket.error as e:
            print(f"[ERROR] Koneksi {device_name} gagal: {e}")
            break
        except Exception as e:
            print(f"[ERROR] Kesalahan tak terduga pada {device_name}: {e}")
            break

def handle_status(device_name, status, value):
    """Menangani pesan status dari ESP32 selama kalibrasi dan menampilkan sudut."""
    global is_calibrating, thoracic_ready, lumbar_ready, calibration_status_granular

    is_ready = False
    
    if status == "POSISI_SALAH_ATAS":
        is_ready = False
        if value is not None:
            # Tentukan batas berdasarkan nama perangkat (hardcoded dari C++)
            limit = 100 if device_name == "Lumbar" else 98
            error = value - limit # Error = Nilai - Batas Atas
            calibration_status_granular[device_name]["Atas"] = f"SALAH (Error: {error:+.2f}°)"
        else:
            calibration_status_granular[device_name]["Atas"] = "SALAH"
        # Asumsi: Jika Atas salah, Bawah OK (untuk tampilan)
        if calibration_status_granular[device_name]["Bawah"] == "Menunggu...":
             calibration_status_granular[device_name]["Bawah"] = "OK"

    elif status == "POSISI_SALAH_BAWAH":
        is_ready = False
        # Asumsi: Jika Bawah salah, Atas OK (untuk tampilan)
        if calibration_status_granular[device_name]["Atas"] == "Menunggu...":
            calibration_status_granular[device_name]["Atas"] = "OK" 
        if value is not None:
            # Tentukan batas berdasarkan nama perangkat (hardcoded dari C++)
            if device_name == "Lumbar":
                limit = 90 if value > 90 else 69
                error = value - limit # Error = Nilai - Batas
            else: # Thoracic
                limit = 110 if value > 110 else 88
                error = value - limit # Error = Nilai - Batas
            calibration_status_granular[device_name]["Bawah"] = f"SALAH (Error: {error:+.2f}°)"
        else:
            calibration_status_granular[device_name]["Bawah"] = "SALAH"

    elif status == "SIAP_REFERENSI":
        is_ready = True
        calibration_status_granular[device_name]["Atas"] = "SIAP"
        calibration_status_granular[device_name]["Bawah"] = "SIAP"
    
    elif status == "OK":
        # Status "OK" dikirim setelah 'Y', kita bisa abaikan
        pass

    # Update status global (ready flag)
    if device_name == "Thoracic":
        thoracic_ready = is_ready
    elif device_name == "Lumbar":
        lumbar_ready = is_ready
    

# --- FUNGSI UTAMA ALUR PROGRAM ---

def calibration_workflow():
    """Mengelola alur kalibrasi."""
    global is_calibrating, current_data, is_sampling, thoracic_ref, lumbar_ref, is_running
    global thoracic_ready, lumbar_ready, calibration_status_granular

    print("\n" + "="*70)
    print("                Lakukan kalibrasi terlebih dahulu. Tekan [c] untuk memulai kalibrasi.")
    print("="*70)
    
    while is_running: # Loop utama untuk input 'c' atau 'q'
        user_input = input("> ").strip().lower()

        if user_input == 'c':
            if thoracic_socket and lumbar_socket:
                is_calibrating = True
                # Reset status flags setiap kali kalibrasi dimulai
                thoracic_ready = False
                lumbar_ready = False
                calibration_status_granular = {
                    "Thoracic": {"Atas": "Menunggu...", "Bawah": "Menunggu..."},
                    "Lumbar":   {"Atas": "Menunggu...", "Bawah": "Menunggu..."}
                }
                
                print("\n[INFO] Mengirim perintah kalibrasi ke ESP32. SILAKAN LAKUKAN WALL TEST.")
                send_command(thoracic_socket, 'C')
                send_command(lumbar_socket, 'C')
                
                print("[INFO] Memantau status kalibrasi... (Akan konfirmasi otomatis saat SIAP)")
                
                both_ready_and_confirmed = False
                
                while is_calibrating and is_running and not both_ready_and_confirmed:
                    
                    # Hapus layar untuk update
                    os.system('cls' if os.name == 'nt' else 'clear')
                    print("[INFO] Memantau status kalibrasi... (Akan konfirmasi otomatis saat SIAP)")
                    print("="*70)
                    # Cetak status granular
                    t_atas = calibration_status_granular['Thoracic']['Atas']
                    t_bawah = calibration_status_granular['Thoracic']['Bawah']
                    l_atas = calibration_status_granular['Lumbar']['Atas']
                    l_bawah = calibration_status_granular['Lumbar']['Bawah']
                    
                    # Format string agar rapi
                    print(f"Thoracic : ATAS: {t_atas:<25} | BAWAH: {t_bawah:<25}")
                    print(f"Lumbar   : ATAS: {l_atas:<25} | BAWAH: {l_bawah:<25}")
                    print("="*70)

                    # Cek kondisi auto-konfirmasi
                    if thoracic_ready and lumbar_ready:
                        # Cetak status final sekali lagi sebelum konfirmasi
                        os.system('cls' if os.name == 'nt' else 'clear')
                        print("[INFO] Memantau status kalibrasi... (Akan konfirmasi otomatis saat SIAP)")
                        print("="*70)
                        print(f"Thoracic : ATAS: {t_atas:<25} | BAWAH: {t_bawah:<25}")
                        print(f"Lumbar   : ATAS: {l_atas:<25} | BAWAH: {l_bawah:<25}")
                        print("="*70)

                        print("\n" + "="*70)
                        print("[BERHASIL] Kedua perangkat SIAP. Mengirim konfirmasi ('Y') secara otomatis.")
                        
                        send_command(thoracic_socket, 'Y')
                        send_command(lumbar_socket, 'Y')
                        is_calibrating = False # Selesai kalibrasi
                        both_ready_and_confirmed = True # Keluar dari loop
                        
                        print("[INFO] Menunggu data referensi dari ESP32...")
                        time.sleep(1.5) # Beri waktu ESP32 untuk mengirim data REF:
                        
                        # Cetak referensi yang diterima
                        if thoracic_ref:
                            print(f"[REFERENSI THORACIC] Sagital: {thoracic_ref[0]:.2f}, Coronal: {thoracic_ref[1]:.2f}")
                        if lumbar_ref:
                            print(f"[REFERENSI LUMBAR] Sagital: {lumbar_ref[0]:.2f}, Coronal: {lumbar_ref[1]:.2f}")
                        
                        print("="*70 + "\n")
                    
                    time.sleep(1.0) # Update setiap 1 detik sesuai permintaan
                
                # Jika loop dihentikan (misal 'q') tapi belum selesai
                if not both_ready_and_confirmed and not is_running:
                    print("\n[INFO] Kalibrasi dihentikan oleh pengguna.")
                    is_calibrating = False
                    send_command(thoracic_socket, 'P')
                    send_command(lumbar_socket, 'P')
                
                # Jika sukses, keluar dari loop `while True` besar
                if both_ready_and_confirmed:
                    break

            else:
                print("[ERROR] Kedua perangkat belum terhubung. Coba lagi.")
        
        elif user_input == 'q':
            is_running = False
            return # Keluar dari fungsi

    # --- BAGIAN INI TIDAK BERUBAH ---
    print("\n" + "="*70)
    print("Kalibrasi selesai! Untuk memulai pengambilan sampel, tekan [s].")
    print("="*70)
    
    while is_running:
        user_input = input("> ").strip().lower()
        if user_input == 's':
            is_sampling = True
            send_command(thoracic_socket, 'S')
            send_command(lumbar_socket, 'S')
            break
        elif user_input == 'q':
            is_running = False
            return
        else:
            print("Perintah tidak valid. Tekan [s] untuk memulai atau [q] untuk keluar.")

def data_sampling_and_logging():
    """Loop utama untuk pengambilan sampel data dan interaksi pengguna."""
    global is_sampling, data_buffer, is_running
    
    start_time = time.time()
    
    print("\n" + "="*70)
    print("PENGAMBILAN SAMPEL DIMULAI...")
    print("Tekan [p] (lalu Enter) kapan saja untuk menjeda dan menyimpan data.")
    print("Tekan [q] (lalu Enter) kapan saja untuk keluar tanpa menyimpan.")
    print("="*70)
    
    # Header diubah untuk mencerminkan data "selisih" (diff)
    print(f"{'Waktu (s)':<10} | {'T_Sag_diff':<10} | {'T_Cor_diff':<10} | {'L_Sag_diff':<10} | {'L_Cor_diff':<10}")
    print("-" * 70)

    # Memulai thread untuk input pengguna (agar tidak memblokir loop sampling)
    input_thread = threading.Thread(target=user_input_handler, daemon=True)
    input_thread.start()
    
    last_print_time = 0
    while is_running:
        if is_sampling and all(val is not None for val in current_data):
            current_time = time.time()
            if current_time - last_print_time >= 1.0: # Pastikan print setiap 1 detik
                last_print_time = current_time
                elapsed_time = round(current_time - start_time)
                
                # Data yang akan disimpan dan ditampilkan
                log_entry = [
                    elapsed_time,
                    current_data[0], current_data[1], 
                    current_data[2], current_data[3]
                ]
                
                data_buffer.append(log_entry)

                # Print ke konsol (setiap 1 detik sesuai permintaan)
                print(f"{elapsed_time:<10} | {current_data[0]:<10.2f} | {current_data[1]:<10.2f} | {current_data[2]:<10.2f} | {current_data[3]:<10.2f}", flush=True)

        elif not is_sampling:
            # Dihentikan oleh 'p'
            break
        elif not is_running:
            # Dihentikan oleh 'q'
            break
        else:
            # Tunggu hingga semua data pertama diterima
            time.sleep(0.1)

def user_input_handler():
    """Menangani input pengguna secara non-blocking."""
    global is_sampling, is_running

    while is_running:
        try:
            # Menggunakan sys.stdin untuk membaca dari terminal secara interaktif
            user_input = sys.stdin.readline().strip().lower()
            if user_input == 'p' and is_sampling:
                is_sampling = False
                send_command(thoracic_socket, 'P')
                send_command(lumbar_socket, 'P')
                print("\n[INFO] Pengambilan sampel dihentikan. Memproses penyimpanan CSV...")
                break
            elif user_input == 'q':
                is_running = False
                if is_sampling:
                    # Jika sampling sedang berjalan, kirim 'P' juga
                    send_command(thoracic_socket, 'P')
                    send_command(lumbar_socket, 'P')
                print("\n[INFO] Perintah keluar diterima.")
                break
        except EOFError:
            # Input stream ditutup (misalnya, Ctrl+D)
            is_running = False
            break
        except Exception:
            # Mengabaikan error input lainnya
            if is_running:
                pass

def save_to_csv():
    """Menyimpan data buffer ke berkas CSV."""
    if not data_buffer:
        print("[INFO] Tidak ada data untuk disimpan.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"posture_data_{timestamp}.csv"
    
    # Header diubah untuk mencerminkan data "selisih" (diff)
    header = ['waktu (s)', 'thoracic_sagital_diff', 'thoracic_coronal_diff', 'lumbar_sagital_diff', 'lumbar_coronal_diff']
    
    try:
        with open(filename, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(data_buffer)
        
        print(f"\n[BERHASIL] Data telah disimpan ke: {filename}")
        print(f"Total {len(data_buffer)} baris data dicatat.")
    except Exception as e:
        print(f"[ERROR] Gagal menyimpan berkas CSV: {e}")

# --- FUNGSI UTAMA (MAIN) ---

def main():
    global thoracic_socket, lumbar_socket, is_running

    print("--- ESP32 Dual Posture Logger (Wi-Fi TCP) ---")
    
    # 1. Koneksi
    thoracic_socket = connect_esp(THORACIC_IP, THORACIC_PORT)
    lumbar_socket = connect_esp(LUMBAR_IP, LUMBAR_PORT)

    if not thoracic_socket or not lumbar_socket:
        print("\n[KELUAR] Gagal menghubungkan ke salah satu atau kedua ESP32. Harap perbaiki masalah koneksi.")
        return
    
    # 2. Mulai Thread Penerima Data
    thoracic_thread = threading.Thread(target=receive_data, args=(thoracic_socket, "Thoracic", 0), daemon=True)
    lumbar_thread = threading.Thread(target=receive_data, args=(lumbar_socket, "Lumbar", 2), daemon=True)
    
    thoracic_thread.start()
    lumbar_thread.start()

    # 3. Alur Kalibrasi
    calibration_workflow()

    # 4. Alur Pengambilan Sampel
    if is_sampling and is_running:
        data_sampling_and_logging()

    # 5. Penyimpanan dan Penutupan
    if is_running:
        # Jika keluar secara normal (tekan 'p')
        save_to_csv()
    else:
        # Jika keluar dengan 'q'
        print("[INFO] Keluar tanpa menyimpan data.")

    print("\n[INFO] Menutup koneksi...")
    is_running = False
    if thoracic_socket: thoracic_socket.close()
    if lumbar_socket: lumbar_socket.close()
    
    print("Aplikasi dimatikan.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[KELUAR] Dibatalkan oleh pengguna (Ctrl+C).")
        is_running = False
        if thoracic_socket: thoracic_socket.close()
        if lumbar_socket: lumbar_socket.close()
    except Exception as e:
        print(f"[FATAL ERROR] Terjadi kesalahan: {e}")

