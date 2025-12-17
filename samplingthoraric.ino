#include <WiFi.h>
#include <WiFiClient.h>
#include <Wire.h> 

// --- KONFIGURASI WIFI DAN SOCKET ---
const char* ssid = "TestingAlat"; // <--- GANTI DENGAN NAMA WIFI ANDA
const char* password = "reinhard009"; // <--- GANTI DENGAN PASSWORD WIFI ANDA 
WiFiServer server(8001); // Server TCP untuk Thoracic, Port 8001
WiFiClient client;
bool is_connected = false;

// GPIO PARAMETER
#define CMD 1   // GPIO FOR CALIBRATION COMMAND 
#define DATA 2  // GPIO FOR DETECTION COMMAND 

// PARAMETER FOR DETECTION CRITERION SELECTION 
#define factor 1 // 1 FOR UPPER BACK MCU 
// #define THRESHOLD 10 // <-- Tidak diperlukan lagi

// GLOBAL PARAMETER FOR IMU 
const int MPU_addr_1 = 0x68; 
const int MPU_addr_2 = 0x69; 
int16_t AcX, AcY, AcZ; 
int minVal = 265; 
int maxVal = 402; 
double y_1, z_1; 
double y_2; // z_2 tidak digunakan di Thoracic

// GLOBAL VARIABLE FOR CALCULATION BUFFER 
double T_sagital; 
double T_coronal; 

// GLOBAL VARIABLE FOR REFERENCE VALUE 
double ref_sagital = 0.0; 
double ref_koronal = 0.0; 
volatile bool calibration_pending = false; 
bool sampling_active = false;
unsigned long last_send_time = 0;
const long interval = 1000; // 1 detik

// Variabel untuk Serial Print Debug
unsigned long last_serial_print_time = 0;
const long serial_print_interval = 1000; // 1 detik

// --- FUNGSI UTILITY ---

int convertToThreeDegreeStep(int angle) { 
  angle = angle % 360; 
  return ((angle + 1) / 3) * 3; 
} 

void read_imu_data(int MPU_addr, double &y_out, double &z_out) {
    Wire.beginTransmission(MPU_addr); 
    Wire.write(0x3B); 
    Wire.endTransmission(false); 
    Wire.requestFrom(MPU_addr, 14, true); 
    
    AcX = Wire.read() << 8 | Wire.read(); 
    AcY = Wire.read() << 8 | Wire.read(); 
    AcZ = Wire.read() << 8 | Wire.read(); 
    
    for (int i = 0; i < 8; i++) Wire.read(); 

    int xAng = map(AcX, minVal, maxVal, -90, 90); 
    int yAng = map(AcY, minVal, maxVal, -90, 90); 
    int zAng = map(AcZ, minVal, maxVal, -90, 90); 

    y_out = RAD_TO_DEG * (atan2(-xAng, -AcZ) + PI); 
    z_out = RAD_TO_DEG * (atan2(-AcY, -AcX) + PI); 
}

void calculate_angles() {
    double x_1, z_2_dummy; // z_2 tidak terpakai, tapi func read_imu_data butuh
    read_imu_data(MPU_addr_1, y_1, z_1); 
    // MPU_addr_2 hanya perlu y_2
    read_imu_data(MPU_addr_2, y_2, z_2_dummy); 
    T_sagital = convertToThreeDegreeStep((int)(factor * (-y_1 + y_2))); 
    T_coronal = convertToThreeDegreeStep((int)z_1); 
    if(T_coronal > 180){ 
      T_coronal = T_coronal - 360; 
    } 
} 

void send_data(const char* data) {
    if (client.connected()) {
        client.println(data);
    }
    Serial.println(data); // <-- TAMBAHAN: Tampilkan juga di Serial Monitor
}

// --- FUNGSI KALIBRASI (PERBAIKAN) ---
void calibration_routine() {
    calculate_angles(); 
    
    bool atas_siap = true;
    bool bawah_siap = true;
    String atas_status = "ATAS SIAP";
    String bawah_status = "BAWAH SIAP";

    // Pengecekan ATAS (y_1) - Blok 1
    if (y_1 > 98) { 
        atas_siap = false;
        atas_status = "ATAS SALAH (ERROR: " + String(98.0 - y_1, 2) + ")";
    } else if (y_1 < 68) {
        atas_siap = false;
        atas_status = "ATAS SALAH (ERROR: " + String(68.0 - y_1, 2) + ")";
    } 
    
    // Pengecekan BAWAH (y_2) - Blok 2 (TERPISAH)
    // INI ADALAH PERBAIKANNYA (mengganti 'else if' menjadi 'if')
    // Batas bawah diubah dari 90 ke 88
    if (y_2 > 110) { 
        bawah_siap = false;
        bawah_status = "BAWAH SALAH (ERROR: " + String(110.0 - y_2, 2) + ")";
    } else if (y_2 < 88) { 
        bawah_siap = false;
        bawah_status = "BAWAH SALAH (ERROR: " + String(88.0 - y_2, 2) + ")";
    } 

    // Kirim status gabungan baru untuk Python
    String status_msg = "STATUS:" + atas_status + "|" + bawah_status;
    send_data(status_msg.c_str());
    
    // Kirim status SIAP_REFERENSI jika keduanya OK
    if (atas_siap && bawah_siap) { 
        send_data("STATUS: SIAP_REFERENSI");
    } 
}
// --- AKHIR PERBAIKAN ---


void finalize_calibration() {
    ref_sagital = T_sagital; 
    ref_koronal = T_coronal; 

    String ref_data = "REF:" + String(ref_sagital, 2) + "," + String(ref_koronal, 2);
    send_data(ref_data.c_str());

    send_data("STATUS: OK");
    Serial.println("Kalibrasi Selesai. Ref Sagital: " + String(ref_sagital));
    calibration_pending = false;
}

// --- SETUP ---
void setup() { 
    Serial.begin(115200); 
    pinMode(CMD, INPUT); 
    pinMode(DATA, OUTPUT); 
    digitalWrite(DATA, LOW); // Pastikan pin DATA mati saat mulai

    // SETUP WIFI
    Serial.print("Menghubungkan ke ");
    Serial.println(ssid);
    WiFi.begin(ssid, password);
    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 20) {
        delay(500);
        Serial.print(".");
        attempts++;
    }
    if (WiFi.status() == WL_CONNECTED) {
        Serial.println("\nWiFi Terhubung.");
        Serial.print("IP Address Server Thoracic: ");
        Serial.println(WiFi.localIP());
        server.begin();
    } else {
        Serial.println("\nGagal Terhubung ke WiFi.");
    }

    // SETUP IMU
    Wire.begin(); 
    Wire.beginTransmission(MPU_addr_1); 
    Wire.write(0x6B); Wire.write(0); Wire.endTransmission(true); 
    Wire.beginTransmission(MPU_addr_1); 
    Wire.write(0x1A); Wire.write(0x06); Wire.endTransmission(true); 
    Wire.beginTransmission(MPU_addr_2); 
    Wire.write(0x6B); Wire.write(0); Wire.endTransmission(true); 
    Wire.beginTransmission(MPU_addr_2); 
    Wire.write(0x1A); Wire.write(0x06); Wire.endTransmission(true); 

    Serial.println("Thoracic Server Siap.");
} 

// --- LOOP UTAMA ---
void loop() { 
    
    unsigned long current_millis = millis();
    if (current_millis - last_serial_print_time >= serial_print_interval) {
        last_serial_print_time = current_millis;
        
        calculate_angles(); 
        
        Serial.print("[DEBUG Thoracic] Sag: ");
        Serial.print(T_sagital, 2);
        Serial.print(" | Cor: ");
        Serial.print(T_coronal, 2);
        // Baris tambahan untuk melihat sudut mentah (Raw Angles)
        Serial.print(" || Raw Angles -> Top(y1): ");
        Serial.print(y_1, 2);
        Serial.print(" | Bottom(y2): ");
        Serial.println(y_2, 2);
    }

    // Sisa kode fungsionalitas utama tetap sama
    if (server.hasClient() && !client.connected()) {
        if (client) client.stop();
        client = server.available();
        Serial.println("Klien Baru Terhubung (Python)");
        is_connected = true;
    }
    
    if (is_connected && client.connected()) {
        while (client.available()) {
            char command = client.read();
            if (command == 'C') {
                calibration_pending = true;
                sampling_active = false;
                Serial.println("Perintah: Kalibrasi Diterima");
            } else if (command == 'Y' && calibration_pending) {
                finalize_calibration();
                Serial.println("Perintah: Konfirmasi Kalibrasi Diterima");
            } else if (command == 'S') {
                sampling_active = true;
                calibration_pending = false;
                Serial.println("Perintah: Mulai Sampling Diterima");
            } else if (command == 'P') {
                sampling_active = false;
                Serial.println("Perintah: Jeda Sampling Diterima");
            }
        }

        if (calibration_pending) {
            calibration_routine();
            delay(500);
        }
        
        if (sampling_active) {
            unsigned long current_time = millis();
            if (current_time - last_send_time >= interval) {
                last_send_time = current_time;
                calculate_angles();
                
                double diff_sagital = T_sagital - ref_sagital;
                double diff_coronal = T_coronal - ref_koronal;
                String data_str = "T:" + String(diff_sagital, 2) + "," + String(diff_coronal, 2);

                send_data(data_str.c_str());
                Serial.println("Sent to Python: " + data_str);
                
                // --- BLOK LOGIKA ALARM (digitalWrite) DIHAPUS ---
                // --- AKHIR PENGHAPUSAN ---
            }
        }
    } else if (is_connected && !client.connected()) {
        Serial.println("Klien Terputus.");
        is_connected = false;
        calibration_pending = false;
        sampling_active = false;
        digitalWrite(DATA, LOW); // Pastikan pin DATA mati saat terputus
    }
    
    delay(10); 
}