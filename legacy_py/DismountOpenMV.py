import ctypes
from ctypes import wintypes

def silent_dismount_openmv():
    print("🔍 시스템에 연결된 드라이브를 스캔합니다...")
    target_drives = []
    
    # 1. OPENMV 드라이브 모두 찾기
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if bitmask & (1 << i):
            drive = f"{chr(65 + i)}:\\"
            vol_name = ctypes.create_unicode_buffer(256)
            # 볼륨 정보(이름) 가져오기
            ctypes.windll.kernel32.GetVolumeInformationW(
                drive, vol_name, ctypes.sizeof(vol_name), None, None, None, None, 0)
            
            if vol_name.value == "OPENMV":
                target_drives.append(drive[:2])  # 예: "E:", "F:"

    if not target_drives:
        print("❌ OPENMV 드라이브를 찾을 수 없습니다.")
        return False

    print(f"🎯 총 {len(target_drives)}개의 OPENMV 드라이브를 발견했습니다: {', '.join(target_drives)}")

    # 2. 윈도우 API를 이용한 Silent Dismount (메시지 없음)
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3
    FSCTL_DISMOUNT_VOLUME = 0x00090020  # 볼륨 강제 언마운트 제어 코드

    all_success = True

    for drive_letter in target_drives:
        print(f"\n🔓 [{drive_letter}] 드라이브 접근 권한 요청 중...")
        
        # 드라이브 직접 제어 핸들 획득
        handle = ctypes.windll.kernel32.CreateFileW(
            f"\\\\.\\{drive_letter}",
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None
        )

        if handle == -1: # INVALID_HANDLE_VALUE (-1)
            print(f"❌ [{drive_letter}] 접근 거부!")
            print("💡 팁: IDE(VSCode/PyCharm)나 명령 프롬프트를 '관리자 권한'으로 실행해 보세요.")
            all_success = False
            continue

        print(f"⚡ [{drive_letter}] 강제 언마운트(FAT 캐시 초기화) 명령 전송 중...")
        returned = wintypes.DWORD()
        result = ctypes.windll.kernel32.DeviceIoControl(
            handle, 
            FSCTL_DISMOUNT_VOLUME, 
            None, 0, None, 0, 
            ctypes.byref(returned), 
            None
        )

        # 핸들 닫기 (필수)
        ctypes.windll.kernel32.CloseHandle(handle)

        if result:
            print(f"✅ [{drive_letter}] 언마운트 성공! 윈도우 FAT 캐시가 완전히 초기화되었습니다.")
        else:
            print(f"❌ [{drive_letter}] 언마운트 실패! 윈도우 탐색기 창이 드라이브를 열고 있거나 사용 중인지 확인하세요.")
            all_success = False

    return all_success

if __name__ == "__main__":
    silent_dismount_openmv()
    # input("\n엔터 키를 누르면 종료합니다...")