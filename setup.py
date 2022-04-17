import os
from setuptools import setup, find_packages

here = os.path.abspath(os.path.dirname(__file__))
README = open(os.path.join(here, 'README.md')).read()
CHANGES = open(os.path.join(here, 'CHANGES.md')).read()

requires = [
    'gevent==20.9.0',
    'ws4py==0.5.1',
    'PyYAML==5.4',
    'pyramid==1.10.4',
    'requests==2.20.0',
    'python-daemon==2.2.3',
    'redis==3.5.2'
]

setup(name='janus-cloud',
      version='0.8.0',
      license='AGPLv3',
      url='https://github.com/OpenSight/janus-cloud',
      description='Janus-cloud is a cluster solution for Janus WebRTC server, by API proxy approach',
      long_description=README + '\n\n' + CHANGES,
      long_description_content_type="text/markdown",
      classifiers=[
          "Development Status :: 3 - Alpha",
          "Framework :: Pyramid",
          "Intended Audience :: System Administrators",
          "Intended Audience :: Developers",
          "Intended Audience :: Education",
          "Intended Audience :: Telecommunications Industry",
          "License :: OSI Approved :: GNU Affero General Public License v3",
          "Operating System :: POSIX :: Linux",
          "Programming Language :: Python",
          "Programming Language :: Python :: 3.5",
          "Programming Language :: Python :: 3.6",
          "Programming Language :: Python :: 3.7",
          "Programming Language :: Python :: 3.8",
          "Topic :: Communications :: Conferencing",
          "Topic :: Communications :: Internet Phone",
          "Topic :: Internet :: WWW/HTTP",
      ],
      author='OpenSight',
      author_email='public@opensight.cn',
      keywords='Janus cloud WebRTC',
      packages=find_packages(),
      include_package_data=True,
      package_data={
          'januscloud': ['certs/mycert.key', 'certs/mycert.pem'],
      },
      data_files=[
          ('opt/janus-cloud/conf', ['conf/janus-proxy.plugin.p2pcall.yml',
                                    'conf/janus-proxy.plugin.videocall.yml',
                                    'conf/janus-proxy.plugin.videoroom.yml',
                                    'conf/janus-proxy.yml',
                                    'conf/janus-sentinel.yml']),
          ('opt/janus-cloud/html', ['html/p2pcalltest.html',
                                    'html/p2pcalltest.js']),
          ('opt/janus-cloud/certs', ['januscloud/certs/mycert.key',
                                     'januscloud/certs/mycert.pem'])
      ],
      zip_safe=False,
      install_requires=requires,
      tests_require=requires,
      entry_points="""
      [console_scripts]
      janus-proxy = januscloud.proxy.main:main
      janus-sentinel = januscloud.sentinel.main:main
      """,
      )
