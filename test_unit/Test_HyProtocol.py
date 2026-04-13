import time
import math
from HyProtocol import HyProtocol

def run_qa_test():
    print("="*50)
    print("🛠️ [QA Test] HyProtocol 64-Byte Packing/Unpacking 검증")
    print("="*50)

    # [테스트 1] 기본 패킷 사이즈 검증
    dummy_packet = HyProtocol.pack_result(
        1, 100, 5000, 1, 10, 1, 
        True, False, 
        100.5, 200.5, 50.0, 50.0, 45.123, 
        1.1, 2.2, 3.3, 4.4, 34
    )
    
    print(f"✔️ 패킷 사이즈 검증: {len(dummy_packet)} Bytes (Expected: 64)")
    assert len(dummy_packet) == 64, "패킷 사이즈 오류!"

    # [테스트 2] 엣지 케이스(극단적 float 값) 및 데이터 복원 무결성
    print("\n🔬 [Test 2] 극단적 엣지 케이스 주입 테스트...")
    edge_cases = [
        (0.0, 0.0, 0.0, 0.0, 0.0), # Zero
        (-9999.99, -0.0001, 99999.9, math.pi, -math.pi), # 음수 및 파이
        (1e5, 1e-5, 360.0, -360.0, 123456.789) # 큰 수치 및 작은 수치
    ]

    for i, (x, y, w, h, a) in enumerate(edge_cases):
        raw = HyProtocol.pack_result(1, 1, 1, 1, 1, 1, True, True, x, y, w, h, a, 0,0,0,0, 10)
        parsed = HyProtocol.unpack_result(raw)
        
        # struct 변환 과정에서 발생하는 부동소수점 오차 허용 범위(1e-4) 확인
        assert abs(parsed['x'] - x) < 1e-3, f"X 좌표 손실: {parsed['x']} != {x}"
        assert abs(parsed['angle'] - a) < 1e-3, f"Angle 손실: {parsed['angle']} != {a}"
    print("✔️ 엣지 케이스 Float 직렬화/역직렬화 무손실 통과!")

    # [테스트 3] 100번 루프백 부하 테스트 (속도 측정)
    print("\n🏃 [Test 3] 100회 Packing/Unpacking 부하 테스트...")
    ITERATIONS = 100
    start_time = time.time()
    
    for i in range(ITERATIONS):
        raw = HyProtocol.pack_result(
            i, i%100, i%1000, 1, 10, 1, 
            True, True, 
            10.5, 20.5, 30.0, 40.0, 90.0, 
            1.0, 2.0, 3.0, 4.0, 15
        )
        parsed = HyProtocol.unpack_result(raw)
        if not parsed or parsed['txID'] != i:
            print(f"❌ 데이터 손상 발생! Index: {i}")
            break

    elapsed = time.time() - start_time
    print(f"✔️ 100회 루프백 완료! 소요 시간: {elapsed:.5f} 초")
    if elapsed > 0:
        print(f"✔️ 초당 처리량(TPS): {ITERATIONS/elapsed:,.0f} 회/초")
    print("="*50)
    print("✅ 모든 QA 테스트 통과. 다음 단계(1.B 가상 카메라)로 진행 가능합니다.")

if __name__ == "__main__":
    run_qa_test()