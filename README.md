# BTicino Classe 100X/300X Intercom Integration for Home Assistant 🚪🔊📞

[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub issues](https://img.shields.io/github/issues/k-the-hidden-hero/bticino_intercom)](https://github.com/k-the-hidden-hero/bticino_intercom/issues)
[![GitHub release (latest by date)](https://img.shields.io/github/v/release/k-the-hidden-hero/bticino_intercom)](https://github.com/k-the-hidden-hero/bticino_intercom/releases/latest)

Integrate your BTicino Classe 100X or 300X video intercom system with Home Assistant! 🏠 This custom component allows you to monitor calls, open doors, and view the status of your intercom devices directly within Home Assistant.

This integration relies on the BTicino/Netatmo cloud API and utilizes a persistent WebSocket connection for real-time updates, particularly for call events. ☁️🔄

> [!IMPORTANT]
> This component works only with **netatmo cloud connected devices**.
>
> If your device works with bticino ~~shitty~~ poor written cloud you
> can just hope for a firmware upgrade.
>
> At the date of writing this (April 2025) they sent out a mail that they will migrate users to a new cloud (probably the NetAtmo one)
>
> If you are in doubt on which firmware you are using look at the app on your phone.
> The right one is called **Home+Security**:
> - [Android](https://play.google.com/store/apps/details?id=com.netatmo.camera)
> - [iOS](https://apps.apple.com/us/app/home-security/id951725393)
>
> If you are using one called **BTicino Door Entry** app I'm sorry.


## ✨ Features

*   **Lock Control:** (`lock` entity) Open external and internal door locks connected to your BTicino system (BNDL modules). Uses optimistic state updates. 🔓
*   **Incoming Call Sensor:** (`binary_sensor` entity) Get notified when someone rings your external unit (BNEU modules). The sensor stays 'on' for a configurable duration (default 30 seconds). 🔔
*   **Last Event Sensor:** (`sensor` entity) See the last relevant call event (Incoming Call, Answered Elsewhere, Terminated) along with its timestamp. Linked to the main bridge module (e.g., BNC1). 📜

## 📋 Prerequisites

*   A compatible BTicino Classe 100X or 300X video intercom system configured and connected to the BTicino/Netatmo cloud.
*   Your BTicino/Netatmo account credentials (username/email and password).
*   Home Assistant instance (obviously! 😉).

## 🛠️ Installation

### Recommended: HACS (Home Assistant Community Store)

1.  Ensure you have [HACS](https://hacs.xyz/) installed.
2.  Go to HACS -> Integrations -> Click the three dots menu -> Custom Repositories.
3.  Enter `https://github.com/k-the-hidden-hero/bticino_intercom` as the repository URL.
4.  Select `Integration` as the category.
5.  Click "Add".
6.  The "BTicino Intercom" integration should now appear. Click "Install".
7.  Restart Home Assistant. 🔄

### Manual Installation

1.  Download the latest release from the [Releases page](https://github.com/k-the-hidden-hero/bticino_intercom/releases).
2.  Copy the `custom_components/bticino_intercom` folder into your Home Assistant `<config>/custom_components/` directory.
3.  Restart Home Assistant. 🔄

## ⚙️ Configuration

Configuration is done entirely through the Home Assistant user interface:

1.  Go to **Settings** -> **Devices & Services**.
2.  Click **+ Add Integration**.
3.  Search for "BTicino Intercom" and select it.
4.  Enter your BTicino/Netatmo **Username (Email)** and **Password**.
5.  Click **Submit**.
6.  If multiple "Homes" are found associated with your account, you will be prompted to select the correct one. 🏡
7.  The integration will set up entities for your compatible locks and external units. 🎉

## 🧩 Supported Entities

The integration currently creates the following entities based on the modules discovered in your selected BTicino Home:

*   **Lock (`lock.external_door`, `lock.internal_door`, etc.):**
    *   Represents a door lock module (type BNDL).
    *   Allows you to `Open` (unlock) the door latch. The state will show as 'Unlocking' briefly due to optimistic updates before reverting to 'Locked' after a short delay (API confirmation follows).
    *   Provides attributes like module ID, firmware version, reachability, etc.
*   **Binary Sensor (`binary_sensor.external_unit_xyz_call`):**
    *   Represents the call status of an external unit module (type BNEU).
    *   State is `on` when a call is actively ringing, `off` otherwise.
    *   The sensor automatically turns `off` after 30 seconds if no termination event is received.
    *   Provides attributes like module ID, firmware version, reachability, etc.
*   **Sensor (`sensor.bridge_xyz_last_event`):**
    *   Represents the last significant call-related event detected via WebSocket or polling.
    *   The state shows the event type (e.g., `incoming_call`, `answered_elsewhere`, `terminated`).
    *   Attributes include the timestamp of the event and the ID/name of the module involved.

*(Entity IDs might vary based on your device names and Home Assistant configuration)*

## 📝 Events

The integration fires events that are recorded in the Home Assistant Logbook:

*   `bticino_intercom_incoming_call`: Fired when a call is detected.
*   `bticino_intercom_answered_elsewhere`: Fired when a call is answered by another device/app.
*   `bticino_intercom_terminated`: Fired when a call is terminated (hung up).

## ⚠️ Known Issues & Limitations

*   **Cloud Dependent:** This integration requires a working internet connection and access to the BTicino/Netatmo cloud services. 🌐
*   **WebSocket Reliability:** Real-time call events depend on a stable WebSocket connection. If the connection drops, events might be delayed until the next poll or missed entirely. The integration attempts to automatically reconnect.
*   **Optimistic Lock State:** The lock entity uses optimistic updates. When you trigger 'Open', the state immediately shows 'Unlocking' and then 'Locked' after a few seconds. The actual lock state is confirmed during the next coordinator update.
*   **API Rate Limits:** Excessive use might potentially lead to temporary blocks by the BTicino API (though specific limits are unknown). The default polling interval is set conservatively.

## 🤝 Contributing & Support

Found a bug or have a feature request? Please open an issue on the [GitHub repository](https://github.com/k-the-hidden-hero/bticino_intercom/issues). Pull requests are also welcome! 🙌

## 🙏 Acknowledgements

*   This integration relies heavily on the fantastic [pybticino](https://github.com/k-the-hidden-hero/pybticino) library. Many thanks to the original author(s)!

## 📜 License

This project is licensed under the MIT License - see the LICENSE file for details (assuming MIT, please add a LICENSE file if you haven't!).
