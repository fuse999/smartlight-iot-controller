# Smart Light IoT Controller

### ENGR 491 – Engineering Capstone I  
**Regent University | Fall 2025**  
**Team Members:** Riley Pence, [Add other members here]  
**Instructor:** Dr. Alfa Nyandoro  

---

## 🔍 Overview
This project is part of the ENGR 491 Capstone at Regent University. It involves designing a system that allows a homeowner to control a light bulb over the Internet using an ESP32 microcontroller and a web-based interface.  

The system enables users to:
- Turn the light **on or off** on demand  
- Adjust the **brightness level**  
- **Schedule** times for automated light control  
- Manage **user permissions** and access control  
- **Track power usage** over time  
- **Log user activity** for auditing  
- Handle **conflicts** when multiple users issue commands simultaneously  

---

## ⚙️ System Architecture
**Hardware:**  
- ESP32-C6 (or similar) microcontroller  
- TRIAC-based dimmer circuit  
- AC-to-DC power regulation  
- Optional power usage sensor (e.g., HLW8012 or ACS712)

**Software:**  
- Python backend using SQLite database  
- Web interface (HTML/CSS/JS or Django/Python) for remote control  
- REST or WebSocket communication between ESP32 and server  

---

## 📁 Repository Structure
smartlight-iot-controller/
├── hardware/ # Schematics, wiring diagrams, component lists
├── firmware/ # ESP32 source code (Arduino/PlatformIO)
├── webapp/ # Backend and web interface
├── docs/ # Design documentation and reports
├── .gitignore
├── LICENSE
└── README.md


---

## 🧩 Features in Development
- [x] Basic ESP32 light on/off control  
- [ ] Brightness dimming via TRIAC circuit  
- [ ] Scheduled control with conflict management  
- [ ] Web dashboard for remote operation  
- [ ] Power usage and user activity logging  

---

## 🚀 Getting Started
To be written...
