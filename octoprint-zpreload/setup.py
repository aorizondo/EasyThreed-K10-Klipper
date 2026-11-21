# coding=utf-8
from setuptools import setup, find_packages

plugin_identifier = "zpreload"
plugin_package = "octoprint_zpreload"

setup(
    name="OctoPrint-ZPreload",
    version="0.3.0",
    description="Precarga física de backlash en Z (doble motor): baja la corriente con M907 antes de invertir el sentido de Z, absorbe la holgura y restaura corriente + posición.",
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
