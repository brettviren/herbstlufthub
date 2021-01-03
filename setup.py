import setuptools

setuptools.setup(
    name="herbstlufthub",
    version="0.0.0",
    author="Brett Viren",
    author_email="brett.viren@gmail.com",
    description="MPMC for herbstlufthwm and other events",
    url="https://brettviren.github.io/herbstlufthub",
    packages=setuptools.find_packages(),
    python_requires='>=3.8',
    install_requires = [
        "click",
        "pyzmq"
    ],
    entry_points = dict(
        console_scripts = [
            'hh = herbstlufthub.__main__:main',
        ]
    ),
)
