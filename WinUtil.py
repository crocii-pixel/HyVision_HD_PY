import ctypes
from ctypes import wintypes

class WinUtil:
    @staticmethod
    def dismount_openmv():
        """
        시스템에 연결된 모든 OPENMV 드라이브를 찾아 
        메시지 없이 조용히 FAT 캐시를 초기화(언마운트)합니다.
        
        Returns:
            all_success (bool): 모든 언마운트가 성공했는지 여부
            logs (list): UI에 출력할 로그 메시지 리스트
        """
        logs = []
        target_drives = []
        
        # 1. OPENMV 드라이브 모두 찾기
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if bitmask & (1 << i):
                drive = f"{chr(65 + i)}:\\"
                vol_name = ctypes.create_unicode_buffer(256)
                ctypes.windll.kernel32.GetVolumeInformationW(
                    drive, vol_name, ctypes.sizeof(vol_name), None, None, None, None, 0)
                
                if vol_name.value == "OPENMV":
                    target_drives.append(drive[:2])  # 예: "E:", "F:"

        if not target_drives:
            logs.append("OPENMV 드라이브를 찾을 수 없습니다.")
            return False, logs

        logs.append(f"총 {len(target_drives)}개의 OPENMV 드라이브 발견: {', '.join(target_drives)}")

        # 2. 윈도우 API를 이용한 Silent Dismount (메시지 없음)
        GENERIC_READ = 0x80000000
        GENERIC_WRITE = 0x40000000
        FILE_SHARE_READ = 0x00000001
        FILE_SHARE_WRITE = 0x00000002
        OPEN_EXISTING = 3
        FSCTL_DISMOUNT_VOLUME = 0x00090020  # 볼륨 강제 언마운트 제어 코드

        all_success = True

        for drive_letter in target_drives:
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
                logs.append(f"[{drive_letter}] 접근 거부! (관리자 권한으로 실행 필요)")
                all_success = False
                continue

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
                logs.append(f"[{drive_letter}] FAT 캐시 초기화(언마운트) 성공!")
            else:
                logs.append(f"[{drive_letter}] 언마운트 실패! (탐색기 열림/사용 중)")
                all_success = False

        return all_success, logs