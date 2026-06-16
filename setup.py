from setuptools import find_packages, setup


setup(
    name="soloavalon",
    version="0.1.0",
    packages=find_packages(include=["backend", "backend.*"]),
    python_requires=">=3.10",
    install_requires=[
        "fastapi>=0.111,<1.0",
        "uvicorn[standard]>=0.30,<1.0",
    ],
    extras_require={
        "dev": [
            "pytest>=8.2,<9.0",
        ],
    },
)
