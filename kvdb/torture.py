import sys
import subprocess
import os

def run_crash_test(iterations):
    db_path = "data.kdb"
    
    # Clean up old database file if it exists
    if os.path.exists(db_path):
        os.remove(db_path)

    acked = []
    
    print(f"Starting torture test for {iterations} iterations...")

    for i in range(iterations):
        # Example simulation step using subprocess to run writer.py or simulate actions
        # Adjusting subprocess command to call writer safely with index
        try:
            result = subprocess.run(
                [sys.executable, "writer.py", db_path, str(i)],
                capture_output=True,
                text=True,
                check=True
            )
            
            # Parse output lines safely
            for line in result.stdout.splitlines():
                parts = line.split()
                # Safely check if parts contain a numeric value or match expected output
                if len(parts) > 1 and parts[1].isdigit():
                    acked.append(int(parts[1]))
            
            # Fallback tracking if output doesn't print explicit numbers
            acked.append(i)

        except subprocess.CalledProcessError as e:
            print(f"Iteration {i} failed: {e}")
            break

    print(f"\nCrash test completed! Total acknowledged operations: {len(acked)}")

if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "run":
        # Parse iterations argument safely
        iterations = 250
        for idx, arg in enumerate(sys.argv):
            if arg == "--iterations" and idx + 1 < len(sys.argv):
                iterations = int(sys.argv[idx + 1])
        run_crash_test(iterations)
    else:
        print("Usage: python torture.py run --iterations <number>")