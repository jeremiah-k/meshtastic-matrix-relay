from setuptools import find_packages, setup

# Read README file with proper resource management
with open("README.md", encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="mmrelay",
    version="1.1.2",
    author="Geoff Whittington, Jeremiah K., and contributors",
    author_email="jeremiahk@gmx.com",
    description="Bridge between Meshtastic mesh networks and Matrix chat rooms",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/geoffwhittington/meshtastic-matrix-relay",
    project_urls={
        "Bug Tracker": "https://github.com/geoffwhittington/meshtastic-matrix-relay/issues"
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Development Status :: 4 - Beta",
        "Topic :: Communications",
    ],
    python_requires=">=3.8",
    install_requires=[
        "meshtastic>=2.6.4",
        "Pillow==11.3.0",
        "matrix-nio==0.25.2",
        "matplotlib==3.10.1",
        "requests==2.32.4",
        "markdown==3.8.2",
        "haversine==2.9.0",
        "schedule==1.2.2",
        "platformdirs==4.3.8",
        "py-staticmaps>=0.4.0",
        "rich==14.0.0",
        "setuptools==80.9.0",
    ],
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    include_package_data=True,
    package_data={"mmrelay.tools": ["sample_config.yaml"]},
    entry_points={"console_scripts": ["mmrelay = mmrelay.cli:main"]},
)
