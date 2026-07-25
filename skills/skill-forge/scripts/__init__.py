# Marks skill-forge/scripts as a package so the forked optimizer can resolve its
# sibling imports (`from scripts.generate_report import ...`, `scripts.utils`, etc.).
# Run the package-form scripts as `python -m scripts.<name>` from the skill-forge/ dir.
