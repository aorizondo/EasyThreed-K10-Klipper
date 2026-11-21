# coding=utf-8
from setuptools import setup, find_packages

plugin_identifier = "boostkit"
plugin_package = "octoprint_boostkit"

setup(
    name="OctoPrint-BoostKit",
    version="0.4.0",
    description="Muchas mejoras para OctoPrint en un solo plugin: panel de control, gráfico de temperatura, hot-tune (M410 + M290 P0), nivelación y visor 3D.",
    author="Antonio",
    license="AGPLv3",
    packages=find_packages(include=[plugin_package, plugin_package + ".*"]),
    include_package_data=True,
    zip_safe=False,
    python_requires=">=3.7,<4",
    install_requires=["OctoPrint"],
    entry_points={
        "octoprint.plugin": ["{0} = {1}".format(plugin_identifier, plugin_package)]
    },
)
