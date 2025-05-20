import os

output_filename = "combined_sprinkler_snapshot.txt"

with open(output_filename, "w") as outfile:
    for filename in sorted(os.listdir(".")):
        if filename.endswith(".py") and filename != output_filename:
            outfile.write(f"\n\n{'='*80}\n")
            outfile.write(f"📄 {filename}\n")
            outfile.write(f"{'='*80}\n\n")
            with open(filename, "r") as f:
                outfile.write(f.read())

print(f"✅ Combined all .py files into: {output_filename}")
